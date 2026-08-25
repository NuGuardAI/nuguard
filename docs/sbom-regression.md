# SBOM enrichment regressions — investigation & fix plan

Source: comparison of `tests/apps/phlox-app/phlox.sbom.enriched-old.json` (previous run)
vs `tests/apps/phlox-app/phlox.sbom.enriched.json` (DeepSeek-V4-Pro run). Three items
were flagged as regressions; each was root-caused against the actual extraction code
before planning a fix below. One of the three turned out not to be a regression on
closer inspection — that's documented too so it isn't accidentally "fixed" later.

## 1. Duplicate low-confidence `api_endpoint_generic` nodes shadowing real endpoints (confirmed regression)

**Symptom**: the new run has 108 `api_endpoint_generic` `API_ENDPOINT` nodes vs 78
before, and effectively 100% of them duplicate an already-detected `fastapi`-adapter
endpoint at low confidence (0.44) with a truncated path, e.g. `/re-embed` (generic,
0.44) alongside the correct `/api/rag/re-embed` (fastapi, 0.864).

**Root cause**: endpoint dedup is name-based — [nuguard/sbom/extractor/core.py](../nuguard/sbom/extractor/core.py)
merges detections by `(component_type, canonical_name)` in `_merge_detection`, and the
per-match API_ENDPOINT grouping block builds `canonical_name = f"endpoint:{method}:{path}"`
directly from the raw regex-captured path. But [nuguard/sbom/adapters/python/fastapi_adapter.py](../nuguard/sbom/adapters/python/fastapi_adapter.py)
(and the Flask adapter) compose their canonical name from the **prefix-resolved** path
(`_global_router_prefixes`, built in the `core.py` pre-pass around the
`app.include_router(router, prefix=...)` cross-file scan). When a route is declared
under a mounted router (almost every route in this app), the generic regex adapter's
raw path (`/re-embed`) never equals the AST adapter's composed path
(`/api/rag/re-embed`), so `_merge_detection` never merges them — the comment in
`core.py` claiming they merge is only true for un-prefixed/root-mounted routes.

**Fix plan**:
1. In `core.py`'s API_ENDPOINT per-match grouping block, resolve the same
   `_global_router_prefixes` index before building `canonical_name`, mirroring what
   `fastapi_adapter.py`/`flask_adapter.py` already do — this needs the regex match's
   originating file path plus a best-effort mapping from file to router variable
   (the generic regex adapter doesn't parse AST, so an exact match isn't always
   possible; a suffix-based fallback is needed regardless, see step 2).
2. As a backstop for cases the prefix index can't resolve (e.g. non-Python routers),
   add a suffix-match dedup pass after all per-file detections are collected: for
   each `api_endpoint_generic` node, if a higher-tier (`fastapi`/`flask`/`nestjs`)
   node exists with the same method whose composed path *ends with* the generic
   node's raw path, drop the generic node (or merge its evidence into the
   higher-tier node) instead of keeping both.
3. Guard against over-merging distinct routes that happen to share a path suffix
   (e.g. `/list` vs `/api/x/list` vs `/api/y/list`) by requiring the suffix match to
   align on a path-segment boundary, not a raw string suffix.

**Files to touch**: `nuguard/sbom/extractor/core.py` (per-match API_ENDPOINT grouping,
~line 1212), possibly a small shared helper in `nuguard/sbom/core/route_patterns.py`.

**Test plan**: add a regression fixture (FastAPI app with a router mounted at a
non-empty prefix) to `nuguard/sbom/tests/test_gap_fill_rounds.py` or a new
`nuguard/sbom/tests/test_endpoint_dedup.py` asserting exactly one `API_ENDPOINT` node
per real route, at the higher (fastapi) confidence — not two.

## 2. Confidence drop across most `fastapi` endpoints (investigated — not a regression)

**Symptom**: most `fastapi`-adapter endpoints dropped from confidence 0.936 to 0.864
(evidence_count 4→3) between the two runs.

**Investigation**: diffed the raw `evidence` list (not just the summary counters) for
an affected node (`Get Patients With Jobs`). In the **old** run this node incorrectly
carried evidence from two different physical routes — `@router.get('/outstanding-jobs')`
(`server/api/patient.py:435`) *and* `@router.get('/list')` (`server/api/patient.py:245`)
— merged into a single node. In the **new** run, `/list` is correctly split out into
its own node (`Get Patients`, `/api/note/list`), and `Get Patients With Jobs` keeps
only its own evidence. The lower evidence count (and thus lower confidence) is the
expected, correct side effect of fixing an over-merge bug, not a new problem.

**Action**: none required. No code change — closing this item. Worth a note in the
gap-fill/dedup regression test from item 1 so this correct behavior (`/list` and
`/outstanding-jobs` as distinct nodes) doesn't get silently re-merged by a future
change to the dedup key.

## 3. `PROMPT Messages` node incorrectly soft-rejected by LLM verification (confirmed regression)

**Symptom**: the `Messages` PROMPT node (evidence identical in both runs: 5 files,
all `ast_prompt_detector`, confidence 0.85 each) dropped from confidence 0.952 to
0.616 in the new run, with `metadata.extras.llm_soft_rejected = true` and reason
*"The code context shows only an import statement referencing 'prompt_detector' as
'ast_prompt_detector'. This is an import without actual usage..."*.

**Root cause**: [nuguard/sbom/core/verification.py](../nuguard/sbom/core/verification.py)
`verify_uncertain_nodes()` builds the LLM verification prompt from a **single** file's
content:

```python
file_path = (
    evidence_list[0].location.path if evidence_list and evidence_list[0].location else ""
)
file_content = (file_contents or {}).get(file_path)
```

For a node with evidence spread across multiple files (this one has 5:
`chat.py`, `pdf_forms.py`, `adaptive_refinement.py`, `jobs.py`, `vector_store.py`),
only `evidence_list[0]`'s file is sampled. In this run, that happened to be a file
where the prompt is referenced via import rather than defined inline, so the LLM
verifier — seeing only an import statement — reasonably concluded there was no real
prompt usage, even though the node's own evidence already shows the actual prompt
`role`/`content` (`"You are a medical document extraction assistant..."`). This
applies to both runs equally; it surfaced this time because the verification LLM
call (DeepSeek-V4-Pro vs. the previous model) judged the same incomplete context
differently — the underlying context-selection bug is pre-existing, not new, but it
is a real gap worth closing since it can flip a correct node to soft-rejected on any
run.

**Fix plan**:
1. In `verify_uncertain_nodes()`, when a node has evidence from multiple files, pick
   the file that actually demonstrates usage rather than always defaulting to
   `evidence_list[0]` — e.g. prefer a file whose evidence detail/snippet contains
   node-specific markers already captured in `metadata.extras` (`content`, `role`
   for PROMPT nodes) over one that doesn't.
2. Failing a single-file heuristic, include short snippets from *all* evidence
   locations (not just the full content of one file) in the verification prompt, so
   the LLM sees every piece of structural evidence instead of a single sample.
3. As a cheaper stopgap, skip verification entirely for nodes whose
   `metadata.extras` already contains concrete captured content (e.g. PROMPT nodes
   with a non-empty `content` field) — the deterministic detector already extracted
   the actual prompt text, so LLM verification adds risk (false-negative rejection)
   without adding information.

**Files to touch**: `nuguard/sbom/core/verification.py` (`verify_uncertain_nodes`,
`build_verification_prompt`).

**Test plan**: add a case to the verification test suite with a node whose evidence
spans multiple files where only a later file contains the real usage — assert the
node is not soft-rejected, or (for the stopgap) assert PROMPT nodes with captured
`content` skip verification entirely.

## Rollout

Fix items 1 and 3 independently (they touch different modules); no need to sequence
them. After each fix, re-run the phlox-app enrichment and re-diff against
`phlox.sbom.enriched-old.json` using the same node/edge comparison approach to confirm:
- Item 1: `api_endpoint_generic` node count drops back toward the old baseline (~1
  genuinely unique generic endpoint, not ~30 duplicates).
- Item 3: `Messages` (and any other multi-file-evidence node) keeps its full
  confidence instead of being soft-rejected.
