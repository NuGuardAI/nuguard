# Implementation plan — docs/sbom-fix2.md

Verified against current code (not doc claims at face value — corrections
noted per section where the doc's stated root cause/location was off).
Order follows the doc's own suggested sequence (§4 → §1 → §5 → §2 → §3 →
§6 → §7), since §1/§5 share one file and are cheap to do together.

## 1. OpenRouter still `FRAMEWORK`; bare-variable `"model"` node — `nuguard/sbom/adapters/typescript/llm_clients.py`

**Correction to the doc:** openrouter.ai baseURL sniffing is **already
implemented** (`_BASE_URL_TO_PROVIDER` table, line 157) — the doc's "not
yet landed" claim is wrong. The actual bug is downstream: in the
instantiation loop (lines 226–301), when `model_name` can't be resolved
(no literal in the constructor call), the code unconditionally emits a
`FRAMEWORK` node (lines 254–277) even when `base_url_provider` was
successfully resolved to `"openrouter"` moments earlier. The MODEL branch
(303–327) is only reached when a literal model name is found.

The bare-`"model"` node is a second, independent bug in the **call-site**
detection loop (330–386): `_resolve(call, "model", "modelId")` at line
334 falls back from `resolved_arguments` (symbol-table-expanded) to raw
`arguments` (line 78 in `_ts_regex.py`), and for `const model =
options.model || this.getModel('text')` the raw argument text is the
literal identifier `model` — not a placeholder token per `_clean()`
(line 89), so it passes through as a real model name.

**Correction to the doc's "CESScanner" reference:** `CESScanner` is
Google Customer Engagement Suite deployment detection
(`nuguard/sbom/adapters/ces.py`), unrelated to LLM client parsing — the
doc's citation of it as "already-available machinery" is a mistaken
cross-reference. The real machinery is the TS parser's
`resolved_arguments` symbol-table expansion already used throughout
`llm_clients.py`; there's no separate resolution pass to invoke.

**Plan:**
- In the instantiation loop: when `base_url_provider` is resolved but
  `model_name` is empty, still emit `MODEL` (not `FRAMEWORK`) — same shape
  as the existing MODEL branch, with `model_name` left generic (e.g. the
  provider's default identifier is unknown, so this case should emit
  `FRAMEWORK` only when `base_url_provider` is *not* resolved; when it
  *is* resolved via baseURL, emit `MODEL` with `display_name` falling back
  to the provider name and `metadata.extras.model_unresolved = True`, or —
  simpler and consistent with how the doc frames the ask — just fix the
  narrower reported symptom: reclassify `openrouter` baseURL hits from
  `FRAMEWORK` to `MODEL` specifically, keeping other unresolved-model
  providers on the existing `FRAMEWORK` fallback path unless a real model
  literal is absent everywhere.
- In the call-site loop: after resolving `model_name` via `_resolve`,
  reject it if it exactly equals one of the constructor parameter/local
  names commonly used in this pattern (i.e. it wasn't actually resolved to
  a literal) — concretely, only accept a `raw.get(key)` fallback value if
  it looks like a real model identifier (contains `/`, `-`, or a version
  digit) or was returned via `resolved_arguments` (i.e. genuinely
  constant-resolved), not via the bare `raw` passthrough. Implement as a
  small guard in `_resolve` or inline at the call site — whichever keeps
  other adapters' use of `_resolve` unaffected (check all callers of
  `TSFrameworkAdapter._resolve` before tightening its shared behavior;
  prefer a local check in `llm_clients.py` over changing the shared
  helper's semantics).
- Tests in `nuguard/sbom/tests/` (find or create the TS
  `llm_clients` test file): (a) `new OpenAI({ baseURL:
  'https://openrouter.ai/api/v1' })` with no model literal → single
  `MODEL` node, not `FRAMEWORK`; (b) `client.chat.completions.create({
  model })` where `model` is a local variable assigned from a non-literal
  expression → no `MODEL` node emitted for it.

## 2. FRAMEWORK node explosion — one per `@Controller` class — `nuguard/sbom/adapters/typescript/nestjs_adapter.py`

**Confirmed exactly as the doc describes**, and it's the *same file* as
§1's `_compose_path` (convenient to fix together). `NestJSAdapter.extract`
emits one `ComponentDetection(component_type=ComponentType.FRAMEWORK, ...
canonical_name=f"nestjs:framework:{file_path}:{controller_name}")` per
controller class at the end of the per-controller loop (lines 372–383),
purely as the source anchor for each endpoint's `CALLS` relationship hint
(line 361–368) — it is not a generic decorator detector external to this
adapter as the doc speculated; it's self-inflicted by this same adapter.

**Plan:**
- Keep the `RelationshipHint` (endpoints still need a `FRAMEWORK` source
  to hang the `CALLS` edge off), but stop emitting a *node* per
  controller. Two options:
  1. Emit exactly one `FRAMEWORK` node for the whole extraction pass
     (e.g. `canonical_name="framework:nestjs"`, matching the
     one-node-per-framework convention already used elsewhere in
     `llm_clients.py`/other TS adapters), and repoint every
     `RelationshipHint.source_canonical` at that single canonical name
     instead of the per-controller one.
  2. Or drop the node emission entirely and drop the relationship hint
     too, if nothing downstream actually needs a NestJS-presence
     `FRAMEWORK` node (check for consumers of `nestjs:framework:` node
     IDs / any code keying off `metadata.framework == "nestjs"` at the
     controller-FRAMEWORK-node level, not the endpoint level, before
     choosing this).
  Prefer option 1 — cheaper, matches the doc's suggested "app-level
  signal" framing, and preserves the relationship structure other code
  may depend on.
- Regression test in `nuguard/sbom/tests/` (find/create a NestJS adapter
  test file — check for one first): fixture with 3 `@Controller` classes
  → exactly 0 or 1 `FRAMEWORK` node total (not 3), endpoints still
  present and still linked.

## 3. `/api/v1` global prefix missing — `nuguard/sbom/adapters/typescript/nestjs_adapter.py`

**Confirmed as described** — `_compose_path()` (line 188) only combines
`@Controller('prefix')` + method route; nothing in this adapter or
elsewhere in `nuguard/sbom/adapters/typescript/` scans `main.ts` for
`app.setGlobalPrefix(...)`. This is a real gap, not a regression.

**Plan:**
- Add a small regex pre-pass (new function, e.g.
  `_extract_global_prefix(content: str) -> tuple[str, list[str]]` →
  `(prefix, exclude_patterns)`) that matches
  `app.setGlobalPrefix\(\s*['"]([^'"]+)['"]` and, optionally, a following
  `{ exclude: [...] }` array of route strings/objects.
- This needs to run against `main.ts`, a *different file* than the
  controller files `NestJSAdapter.extract()` normally processes one at a
  time. Check how the extractor pipeline provides adapters visibility
  across files (mirrors the existing `set_global_model_schemas` cross-file
  hook already used for DTO schemas at line 205) — add an analogous
  `set_global_route_prefix(prefix: str, exclude: list[str])` hook, wired
  from `extractor/core.py`'s pre-pass (find where
  `set_global_model_schemas` is currently invoked and add a sibling call
  that scans for `main.ts`/`app.setGlobalPrefix` once per extraction run).
- In `_compose_path`, accept an optional outer prefix parameter and apply
  it in addition to (outer to) the per-controller prefix, honoring
  `exclude` via a best-effort literal/prefix string match against the
  composed path (no full glob semantics needed, per the doc).
- Regression fixture: minimal `main.ts` with
  `app.setGlobalPrefix('api/v1')` + one controller file → composed
  endpoint path includes `/api/v1/...`.
- After landing, regenerate
  `tests/apps/studyield-app/studyield.sbom.json` /
  `.enriched.json` and spot-check `/api/v1/auth/login` etc. appear.

## 4. AUTH evidence citing import/DTO lines; Apple JWKS missing

**Correction to the doc:** there is **no dedicated TS/NestJS AUTH
detector** analogous to `fastapi_adapter.py`'s `_AUTH_STRICT_CLASSES`
AST-based instantiation matching (confirmed: no TS file under
`nuguard/sbom/adapters/typescript/` references OAuth2/JWT/jwks at all).
The `JWT`/`OAuth2`/`Bearer Auth` nodes currently in Studyield's SBOM come
from the **LLM gap-fill pass**
(`nuguard/sbom/core/gap_fill/categories.py` lists `ComponentType.AUTH` as
a gap-fill category, "high recall without LLM" — i.e. it's seeded/refined
by an LLM call against arbitrary file content, not a deterministic AST
match). That explains the symptom precisely: gap-fill's LLM found *a*
plausible line (an import, a DTO field) rather than the true call site,
because there's no structural detector telling it where to look.

This means the doc's "port `_AUTH_STRICT_CLASSES`'s instantiation-over-
import preference" framing doesn't apply as literally as written — that
preference only exists inside a **deterministic AST/regex detector**, and
none exists for TS auth today. The real fix is closer to §3 of
`sbom-fix2.md`'s TOOL section: **write a new small, regex-based TS AUTH
detector** (mirroring `nestjs_tool_di.py`'s file-scan-and-match style,
not `fastapi_adapter.py`'s AST-walk style, since TS decorator/AST support
here is regex-based per `nestjs_adapter.py`'s own module docstring) that
finds real instantiation/call sites for a small class table:
- `new OAuth2Client(...)` / `.verifyIdToken(...)` (from
  `google-auth-library`) → `OAuth2` node.
- `jwksClient(...)` / `new JwksClient(...)` (from `jwks-rsa`), keyed by
  `jwksUri` value, so an `appleid.apple.com` URI produces a distinctly
  named node (e.g. `apple-sign-in-jwks`) vs. a generic JWKS node for other
  issuers.
- `jwt.sign(...)` / a `generateTokens`/`refreshToken`-named method body
  containing a JWT-signing call → `JWT` node, evidence at the signing call
  site, not any `import jwt` line.
- New detector emits `evidence_kind="regex"` detections the same shape as
  `nestjs_tool_di.py`, so gap-fill's LLM-AUTH pass naturally has less to
  guess once real nodes exist for these mechanisms (check whether
  gap-fill already skips a category once a deterministic detector has
  emitted nodes for it — likely yes, same as other categories with both a
  static adapter and a gap-fill fallback; confirm during implementation).
- Regression test: fixture file with an `import { OAuth2Client } from
  'google-auth-library'` line, a DTO field named `oauth2Token`, and the
  real `new OAuth2Client(...).verifyIdToken(...)` call in the same file
  → asserts the AUTH node's evidence location is the instantiation line,
  not the import or DTO field.

## 5. TOOL: code-sandbox missing; Generic FP; Web Search evidence wrong file — `nuguard/sbom/adapters/typescript/nestjs_tool_di.py`

**Confirmed root cause for code-sandbox:** `NestJSToolDIAdapter.extract`
requires `has_llm_sibling` (line 185) — an LLM-client-typed constructor
parameter in the *same* class as the candidate tool. If
`CodeSandboxService` is injected into a controller/service that doesn't
itself also inject an `AiService`-like class directly (e.g. it's one hop
further from the LLM-facing service), this adapter structurally can't
fire for it. Note `"sandbox"` is *already* in `_ACTION_VERBS` (line 60) —
so the type-name-matching half of the heuristic already covers
`CodeSandboxService`; only the `AiService`-adjacency requirement blocks
it.

**Plan:**
- Add a second, independent firing condition alongside the existing
  `has_llm_sibling` check: a class method that (a) is reachable from a
  `@UseGuards`-protected controller endpoint (or, more cheaply/regex-
  feasible: the class itself is `@Injectable()` and referenced from a
  controller with `@UseGuards`) and (b) issues an outbound `fetch(`/
  `axios.`/`http.request(`/known-SDK-client call whose URL argument
  references a config/env-derived variable (e.g. `${sandboxUrl}` /
  `this.configService.get(...)`), per the doc's own scoping (already
  matches `studyield-sbom-fix.md` item #3, cited as previously proposed).
  This is a distinct code path from the existing DI-sibling heuristic —
  add it as a second scan in the same adapter rather than loosening
  `has_llm_sibling` (loosening that check broadly would raise
  false-positive risk for every other DI-based match).
- Set `metadata.extras.privilege_scope` (or whatever field
  `PrivilegeScope` values are stored under — check
  `nuguard/sbom/types.py` and existing TOOL nodes for the convention) to
  `CODE_EXECUTION` when the outbound call forwards free-form user input as
  executable code/commands in the request body — reuse the existing enum,
  don't invent a new signal, per the doc.
- Fix the `exam-clone.service.ts:898` "Generic" false positive: require
  actual outbound-call syntax (`fetch(`, `axios.`, `http.request(`, or a
  recognized SDK client method call already known to `llm_clients.py`'s
  provider tables) within the matched method's body before emitting a
  TOOL node from *any* generic/DI-heuristic tier — locate exactly which
  tier emits the `"Generic"`-named node (search for a lower-confidence
  fallback path, likely in this same file or a sibling generic-tool
  detector under `nuguard/sbom/adapters/` — confirm during implementation
  since it wasn't pinned down in this research pass) and add the same
  call-syntax gate there.
- Web Search evidence (`research.service.ts:71` vs. the real
  `fetch('https://api.tavily.com/search', ...)` at
  `web-search.service.ts:33`): same "prefer the call site with actual
  outbound-call syntax over whatever DI-adjacent class the heuristic
  latches onto" fix as above — once the call-syntax gate exists, re-rank
  candidate matches so the class actually containing the `fetch()` call
  wins over one merely DI-adjacent to it.
- Regression tests: fixture with `CodeSandboxService` two hops from
  `AiService` but containing a `fetch(\`${sandboxUrl}/execute\`, ...)`
  call → TOOL node emitted, `CODE_EXECUTION` privilege set. Fixture
  mirroring `exam-clone.service.ts:898`'s shape (DI-heuristic-eligible
  class/method with no outbound-call syntax) → no TOOL node.

## 6. Hand-rolled multi-agent orchestration undetected

**Confirmed** — the current `"Studyield Assistant"` AGENT node is the
generic fallback synthesized in `nuguard/sbom/extractor/core.py` (~line
1707–1732: "Synthesize a fallback AGENT node representing the app itself
when no [other AGENT nodes exist]"), tagged
`metadata.extras.source: "auto_enrichment"`, not a real detection of the
`BaseAgent`/`ProblemSolverService` sequential-orchestration pattern.

**Plan** (largest/most novel item — do last, as the doc suggests, once
§4/§5's "prefer real call site over generic match" pattern is
established):
- New shared, language-agnostic detector (e.g.
  `nuguard/sbom/adapters/generic_agent.py`, wired into both Python and TS
  adapter registries per the doc) that looks for: an abstract/base class
  whose subclasses are each instantiated and invoked in sequence from one
  constructor/method in another class, where each call path reaches a
  recognized LLM-client call (anything `llm_clients.py`'s provider tables
  already recognize, in either language) within a small hop count.
  TS-side, this needs its own regex-based class/subclass/sequencing scan
  (no shared AST base to lean on, consistent with how `nestjs_adapter.py`
  and `nestjs_tool_di.py` already operate on this codebase).
- Emit one `AGENT` node named for the orchestrating service, with real
  evidence (base-class definition site + the sequencing call site) and
  `metadata.extras.detected_by_tiers` marking it a heuristic (confidence
  0.5–0.6, per the doc), distinguishable from framework-native AGENT
  nodes.
- Gate the `extractor/core.py` fallback-AGENT-synthesis block (~line
  1719: `if not any(n.component_type == ComponentType.AGENT for n in
  doc.nodes) and any(...)`) so it doesn't fire when this new heuristic
  already produced a real AGENT node for the same class — this should
  already hold structurally (`not any(... AGENT ...)` short-circuits once
  a real node exists), so mainly needs a regression test confirming
  ordering (new detector must run, and its node must be added to
  `doc.nodes`, before this fallback check executes).
- Regression test: fixture with a `BaseAgent` abstract class and 2–3
  subclasses instantiated/invoked in sequence from an orchestrator class
  that also reaches a recognized LLM-client call → one real AGENT node
  with non-empty evidence; the generic fallback does not also fire.

## 7. DEPLOYMENT fragmentation (3 nodes vs. 1)

**Confirmed unchanged** — `nuguard/sbom/adapters/nginx.py` and
`nuguard/sbom/adapters/iac.py` (GitHub Actions adapter included) each emit
independent `DEPLOYMENT` nodes per keyword/file match; no
`docker-compose.yml`-aware consolidation adapter exists in
`nuguard/sbom/adapters/`.

**Plan** (lowest priority — pick up only if time remains, per the doc):
- Add a `docker_compose.py` adapter (or extend an existing one) that,
  when a `docker-compose.yml` is present, parses its service list and
  emits one consolidated `DEPLOYMENT` node keyed off that file, then
  suppresses/merges the separate `docker`/`nginx`/CI-triggered-deploy
  keyword hits that would otherwise fire for files already represented by
  a service in that compose file (mirrors the general "prefer a real
  structural source of truth over independent keyword matches" pattern
  used throughout the rest of this plan).
- Regression test: fixture repo with `docker-compose.yml` (N services)
  and a matching `Dockerfile`/nginx config already covered by it → 1
  `DEPLOYMENT` node, not 1-per-file.

## Suggested order of work

1. §1 (OpenRouter/MODEL) — smallest, isolated, single file.
2. §2 + §3 together (same file, `nestjs_adapter.py`) — §2 is a small
   suppression, §3 needs the new cross-file hook; land §2 first as a
   quick win, then §3.
3. §4 (AUTH detector) — new detector, moderate size, unblocks the
   "prefer real call site" pattern reused in §5.
4. §5 (TOOL: code-sandbox + Generic FP + Web Search evidence) — three
   related fixes to one file, reuses §4's evidence-ranking pattern.
5. §6 (hand-rolled AGENT) — largest, most novel, do last.
6. §7 (DEPLOYMENT) — only if time remains.

Each fix ships with a regression test colocated under
`nuguard/sbom/tests/`, plus (where feasible) a final re-check of
`tests/apps/studyield-app/studyield.sbom.enriched.json` against
`studyield.ground-truth.sbom.json` confirming the specific symptom is
resolved, per the doc's own closing instruction.

## Verification

- `uv run pytest nuguard/sbom/ -v`
- `uv run ruff check nuguard/`
- `uv run mypy nuguard/` (baseline: confirm current error count before
  starting, same discipline as the redteam-test-fix.md session)
- Regenerate `tests/apps/studyield-app/studyield.sbom.json` /
  `.enriched.json` after §1–§7 land, diff against
  `studyield.ground-truth.sbom.json` for the specific symptoms this plan
  targets.
