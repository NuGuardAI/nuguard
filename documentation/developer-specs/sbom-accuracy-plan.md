# NuGuard SBOM — Accuracy Improvement Plan

## Context

Auditing a real-world SBOM (`anomalyco/opencode`, a large multi-package
TypeScript/Bun monorepo — see `tests/apps/open-code/opencode.sbom.json`, 1128
nodes) against the source repo surfaced several accuracy gaps. None of them
are opencode-specific — each is a class of false positive/negative that any
sufficiently large or config-driven repo will trigger. This plan describes
generic fixes, not opencode-specific patches.

## Findings

### 1. Bulk data-catalog fixture files inflate MODEL (and similar) counts

`model_generic`'s regex/AST detector fires once per string literal that looks
like a model name, with no awareness that a single file can be a **enumerable
data catalog** rather than code. In this SBOM, a single test fixture
(`packages/opencode/test/tool/fixtures/models-api.json`, a mocked
models-listing API response) accounts for the large majority of the 687
`MODEL` nodes — one node per catalog entry (`bge-base-en-v1.5`,
`bge-reranker-v2-m3`, `allenai/olmo-3-32b-think`, …), none of which represent
a model the application actually calls.

nuguard's LLM verification pass *does* catch many of these
(`llm_soft_rejected: true`, reason "test fixture … not production model
instantiation") — but only 6 of 8 near-identical siblings extracted from the
exact same file/line-range got flagged; two did not, despite being
indistinguishable from the rejected ones by any signal available to the
verifier. That's a precision-fragility problem, not just a filtering-scope
problem: the same input should not get inconsistent verdicts. This SBOM also
contains at least one clearly garbled entry (`"chat:chat"`) suggesting a
JSON-key-path flattening bug feeds bogus strings into the same detector.

**Generic fix:**
- Add a structural, deterministic (non-LLM) pre-filter: when a single
  file/detector pair produces node counts above a threshold (e.g. >15 matches
  in one file), treat the file as a **data catalog** rather than code and
  either (a) collapse it into one summary node ("N models referenced in
  models-api.json (test fixture)") or (b) cap emission and mark the rest
  `bulk_catalog_truncated` — cheaper and more consistent than relying on N
  independent LLM calls to reach the same "this is a fixture" verdict.
- This same fix generalizes to any bulk-enumerable non-code data file:
  models.dev catalogs, i18n locale bundles (see #2), OpenAPI specs with
  hundreds of paths, etc. — the detector layer should recognize "large flat
  list of similar literals in one non-source-of-truth file" as a shape,
  independent of which component type it happens to match.
- Fix (or add a regression test pin for) the JSON-key flattening bug behind
  garbled names like `"chat:chat"`.

### 2. i18n/locale files produce false-positive, garbled PROMPT nodes

`prompt_ts` matches strings near a key path containing `prompt` (e.g.
`"desktop.updateAfterDownloaded.prompt"`, `"context.systemPrompt.title"`,
`"terminal.prompt.loading"`), which is a UI-copy naming convention, not an
LLM prompt. Because locale bundles repeat the same key across every
supported language, this pattern is multiplied once per locale file
(`i18n/en.ts`, `i18n/fa.ts`, `i18n/uk.ts`, `i18n/vi.ts`, …), and the extracted
`content`/`name` fields are visibly corrupted — they contain fragments of
*adjacent, unrelated* translation strings rather than the matched key's own
value (e.g. `name: "0            Api                    Prompt"`, `content:
"ample.11\": \"بهینه سازی...`), indicating the surrounding-context slice
window used for extraction crosses object-literal key boundaries.

**Generic fix:**
- Path-based exclusion (or steep confidence penalty) for files that are
  structurally i18n/locale bundles — detectable generically by shape (a
  single large flat/nested string-literal object keyed by dotted UI-copy
  identifiers, present once per locale under an `i18n`/`locales`/`lang`
  directory convention) rather than a hardcoded opencode path list.
- Tighten the `prompt` detector's match condition: require the matched
  string's *value* to look like natural-language instruction text addressed
  to a model (imperative/instructional sentence shape, minimum length,
  absence of UI-only tokens), not just a key path containing the substring
  `prompt`.
- Fix the context-extraction window so `content` never crosses into a
  sibling object-literal entry — this is a correctness bug independent of
  the i18n false-positive rate and affects any adjacent-string-literal
  extraction, not just i18n files.
- Fix node-name generation to collapse repeated whitespace/mangled
  camelCase splits (`"Count   Lsp      Prompt"` → `"Count Lsp Prompt"`).

### 3. Config-driven agent architectures aren't detected (framework-agnostic gap)

All `AGENT` nodes came from one adapter (`vercel_ai_sdk_ts`) matching direct
`streamText`/`generateText`/`generateObject` call sites — including several
in unit test files exercising provider-timeout/transform behavior with a
throwaway prompt, not product agents. The application's *actual* agent
architecture — a named-map configuration (`{build: {...}, plan: {...},
general: {...}}`, each entry carrying model/permission/system-prompt fields)
— was invisible to extraction entirely, because no adapter looks for that
shape.

This is a common pattern across coding-agent and multi-agent frameworks
generally (config-object or markdown-file-per-agent definitions), not
specific to Vercel AI SDK or to opencode.

**Generic fix:**
- Add a framework-agnostic "agent registry" adapter: detect a
  dict/record/struct literal (or a directory of one-file-per-agent
  markdown/YAML definitions) whose entries share an "agent config" shape —
  a `model`/`prompt`/`system`/`permission`/`tools` field cluster — and emit
  one AGENT node per entry, independent of which specific SDK or in-house
  framework defines the shape. This is the same category of structural
  pattern-matching as the existing dependency-injection/registry detectors
  already used elsewhere in the extractor; it just needs a schema tuned to
  "agent config," not call-site matching.
- Demote (or require an extra corroborating signal for) AI-SDK call sites
  found under `test/`/`__tests__/`/`*.test.ts` paths before labeling them
  AGENT — the existing "is this a test file" confidence penalty used
  elsewhere in the pipeline isn't being applied to this adapter.

### 4. `llm_soft_rejected` enforcement is inconsistent across consumers

See [NuGuardAI/nuguard#511](https://github.com/NuGuardAI/nuguard/issues/511)
(filed separately). `analysis`, `behavior`, and `policy` correctly exclude
soft-rejected nodes; `redteam`'s scenario generator and the SBOM's own
`node_counts` summary do not, so confirmed false positives are both reported
as if verified and used to generate adversarial scenario content.

## Priority order

1. **#4** (soft-reject enforcement) — cheapest fix (one shared helper +
   a few call sites), highest functional impact (wrong redteam scenarios,
   misleading summary counts) — tracked in issue #511.
2. **#1** (bulk-catalog collapse) — mechanical, testable in isolation,
   directly cuts SBOM noise/cost (fewer LLM verification calls needed) on
   any repo with a large fixture/catalog file.
3. **#2** (i18n/locale false positives + context-extraction bug) — the
   context-window bug is a correctness issue beyond just i18n; worth fixing
   regardless of the locale-specific filter.
4. **#3** (config-driven agent detection) — highest design effort (new
   adapter + schema), but closes a real coverage gap for an increasingly
   common agent-definition pattern across frameworks.

## Non-goals

- Opencode-specific path/name allowlists — every fix above should be framed
  as a structural/shape-based heuristic so it generalizes to the next
  monorepo, not a patch for this one repo.
- Reworking the LLM verification pass's prompt/model — the inconsistency
  noted in #1 is real but the priority is removing the *need* to ask an LLM
  the same "is this a fixture" question hundreds of times per scan, not
  making the LLM more consistent at answering it.
