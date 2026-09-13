# SBOM remediation plan #2 — studyield-app follow-up

**Status: §1–§6 IMPLEMENTED and verified end-to-end against the real
Studyield source** (regenerated `studyield.sbom.json` and confirmed each
symptom below is resolved — see per-section notes for line/evidence
details). Two corrections found during implementation, beyond what was
originally scoped:
- The `app.setGlobalPrefix(...)` pre-pass (§1) had to additionally resolve
  the variable form (`const apiPrefix = configService.get('API_PREFIX',
  'api/v1'); app.setGlobalPrefix(apiPrefix)`), not just a literal argument
  — that's the actual pattern `main.ts` uses.
- The hand-rolled AGENT detector (§6) had to detect NestJS
  constructor-injected agent subclasses (`private readonly analysisAgent:
  AnalysisAgent`, invoked as `this.analysisAgent.execute(...)`), not just
  direct `new XAgent(...)` instantiation — that's the actual pattern
  `ProblemSolverService` uses; a `new X()`-only heuristic would have missed
  it entirely.

**§7 (DEPLOYMENT consolidation) was not attempted** — lowest priority,
already deferred twice across this doc and its predecessors; left for a
future pass.

Follow-up to [sbom-misses.md](sbom-misses.md) and
[tests/apps/studyield-app/studyield-sbom-fix.md](../tests/apps/studyield-app/studyield-sbom-fix.md).
Several items those docs marked "Fixed" were re-checked against
`tests/apps/studyield-app/studyield.sbom.enriched.json` vs.
`tests/apps/studyield-app/studyield.ground-truth.sbom.json` and turned out to
still be wrong — the *counts* moved to match ground truth but the
*evidence/content* didn't. This doc tracks only the items still broken today.

## Summary

| # | Area | Symptom | Status |
|---|---|---|---|
| 1 | API_ENDPOINT | `/api/v1` global prefix missing on all 207 endpoints | Not fixed |
| 2 | AUTH | Evidence cites import/DTO lines, not real call sites; Apple JWKS missing | Regressed from claimed fix |
| 3 | TOOL | Highest-privilege tool (code-sandbox) missing entirely; known FP still present | Not fixed |
| 4 | MODEL/FRAMEWORK | OpenRouter still `FRAMEWORK`; bare-variable `"model"` MODEL node | Regressed from claimed fix |
| 5 | FRAMEWORK | 28 spurious per-controller nodes (0 in ground truth) | New regression |
| 6 | AGENT | Hand-rolled orchestrator not detected; generic LLM placeholder instead | Not fixed |
| 7 | DEPLOYMENT | Still fragmented into 3 nodes vs. 1 in ground truth | Partially fixed (unchanged) |

## 1. `/api/v1` global prefix missing from every extracted endpoint

**Root cause:** `_compose_path()` in
[nuguard/sbom/adapters/typescript/nestjs_adapter.py](../nuguard/sbom/adapters/typescript/nestjs_adapter.py#L188)
composes `@Controller('prefix')` + method-decorator route only. It never
looks for Nest's app-wide prefix, set once in `main.ts` via
`app.setGlobalPrefix('api/v1')`, which applies to every route in the app.
Ground truth's 5 reference endpoints all include the prefix
(`/api/v1/auth/login`, etc.); the enriched SBOM has 0/207 endpoints
prefixed.

**Impact:** doesn't affect `path_param_sources` matching (both sides of a
pair are consistently un-prefixed — see
[docs/redteam-test-fix.md](redteam-test-fix.md)), but it does mean any
consumer that dials the extracted path directly against a real deployment
(behavior/redteam auto-discovery, static analysis IDOR-surface checks that
assume the real route) gets a 404.

**Plan:**
- In `nestjs_adapter.py`, add a small pre-pass that scans `main.ts` (or
  whichever file calls `NestFactory.create` in the entrypoint) for
  `app.setGlobalPrefix('...')` / `app.setGlobalPrefix("...", { exclude: [...] })`,
  extract the prefix string, and thread it through `_compose_path()` as an
  outer prefix applied to every controller's composed path (in addition to,
  not instead of, the per-controller `@Controller('prefix')` prefix).
- Respect the `exclude` option if present (routes listed there should not
  get the global prefix) — best-effort string/path match is sufficient, no
  need for full glob semantics.
- Add a regression fixture: a minimal `main.ts` with
  `app.setGlobalPrefix('api/v1')` + one controller, asserting the composed
  endpoint includes the prefix.

## 2. AUTH nodes cite generic/import evidence instead of real call sites; Apple Sign-In (JWKS) still undetected

**Root cause:** despite [studyield-sbom-fix.md](../tests/apps/studyield-app/studyield-sbom-fix.md)
item #5 describing this exact fix, it was never implemented:
- `JWT` node evidence is `auth.service.ts:8` — an `import` statement, not the
  `generateTokens()`/`refreshToken()` signing call site (ground truth:
  `auth.service.ts:33`).
- `OAuth2` node evidence is `auth/dto/index.ts:66` — a DTO field reference,
  not the actual `new OAuth2Client(GOOGLE_CLIENT_ID)` /
  `.verifyIdToken(...)` call (ground truth: `auth.service.ts:46`).
- No node represents Apple Sign-In at all. Ground truth's
  `apple-sign-in-jwks` (`jwksClient({ jwksUri: 'https://appleid.apple.com/auth/keys', ... })`
  at `auth.service.ts:41`) has no counterpart in the enriched output — the 3
  AUTH nodes present (`Bearer Auth`, `JWT`, `OAuth2`) don't include it under
  any name.

**Plan:**
- Add the OAuth/OIDC client-class table proposed in the original doc
  (`OAuth2Client` from `google-auth-library`, `JwksClient`/`jwksClient` from
  `jwks-rsa`) to whatever TS auth detector currently produces the `JWT`/
  `OAuth2` nodes, so a distinct `apple-sign-in-jwks`-equivalent node gets
  emitted.
- Change the evidence-location preference for all three mechanisms: prefer
  the actual instantiation/signing/verification call site
  (`new OAuth2Client(...)`, `jwksClient(...)`, `jwt.sign(...)`/
  `generateTokens()`) over any `import` line or DTO/type-definition
  reference. This is the same "instantiation over import" preference
  already implemented for `_AUTH_STRICT_CLASSES` in the Python FastAPI
  adapter — port the same call-site-ranking logic rather than reinventing it.
- Regression test: fixture with an `import` line, a DTO field named
  `oauth2Token`, and the real `new OAuth2Client(...).verifyIdToken()` call
  in the same file — assert the AUTH node's evidence location is the
  instantiation, not the import or DTO.

## 3. TOOL: code-sandbox (highest-privilege) missing; known false positive unfixed

**Root cause:**
- `code-sandbox-execution-tool` — ground truth's only `high_privilege: true`
  tool (proxies arbitrary user code to an external HTTP sandbox) — has **no
  corresponding node**. The current `nestjs_tool_di` adapter (used for
  `Knowledge Base` / `Web Search`) detects tools via DI-usage heuristics
  (`detection_basis: "di_usage_heuristic"`, keyed off constructor-injected
  service classes referenced from an `AiService`-adjacent call site) — this
  never fires for `CodeSandboxService`, likely because it's called from a
  dedicated controller/service pair that isn't itself wired through
  `AiService`, unlike the RAG/web-search tools.
- The `"Generic"` node at `exam-clone.service.ts:898` — flagged as a false
  positive in [sbom-misses.md](sbom-misses.md) item #3 and never fixed — is
  still present, still with no outbound-call syntax anywhere near the
  matched line (it's a spaced-repetition SQL query method).
- `Web Search` tool's evidence points to `research.service.ts:71`; ground
  truth's real `fetch('https://api.tavily.com/search', ...)` call site is
  `web-search.service.ts:33` — same file-attribution problem as AUTH above,
  a different (but related) service class matched instead of the one
  issuing the actual HTTP call.

**Plan:**
- Extend the `nestjs_tool_di` adapter (or add a same-shape sibling rule) to
  also fire on: a class method that both (a) is reachable from a
  `@UseGuards`-protected controller endpoint and (b) issues an outbound
  `fetch()`/`axios`/`http.request()` call to a URL built from a config/env
  value — this is the exact heuristic already proposed in
  [studyield-sbom-fix.md](../tests/apps/studyield-app/studyield-sbom-fix.md)
  item #3 and should catch `CodeSandboxService` (fetches
  `${sandboxUrl}/execute`) without needing the `AiService`-adjacency signal
  the DI heuristic currently requires.
- Set `high_privilege: true` when the outbound call forwards free-form user
  input as executable code/commands in the request body (reuse
  `PrivilegeScope.CODE_EXECUTION` from `nuguard/sbom/types.py`, per the
  original plan — do not invent a new signal).
- Require actual outbound-call syntax (`fetch(`, `axios.`, `http.request(`,
  a recognized SDK client method call) within the matched method body before
  emitting a `TOOL` node from the generic/DI-heuristic tier — this directly
  fixes the `exam-clone.service.ts:898` false positive by disqualifying
  matches with no such call.
- For `Web Search`, prefer the call site that actually contains the
  `fetch(...)` call over whatever DI-adjacent class the heuristic currently
  latches onto — same evidence-ranking fix as AUTH item #2, applied here too.

## 4. OpenRouter still `FRAMEWORK` instead of `MODEL`; bare-variable `"model"` node

**Root cause:** [studyield-sbom-fix.md](../tests/apps/studyield-app/studyield-sbom-fix.md)
item #4 described extending `llm_clients.py`'s baseURL-sniffing to recognize
`openrouter.ai/api/v1` and reclassify the match as `MODEL`, plus adding a
guard that a `MODEL` node's name must resolve to a string literal, not a
bare identifier. Neither landed:
- The `Openrouter` node is still `FRAMEWORK`, evidence
  `ai.service.ts:80` (the same `new OpenAI({ baseURL: 'https://openrouter.ai/api/v1' })`
  call the original doc identified).
- A `MODEL` node named literally `"model"` (from
  `const model = options.model || this.getModel('text')` at
  `ai.service.ts:146`, a local variable, not a model-identifier string) is
  still present alongside the correctly-extracted `gpt-4o-mini`/
  `openai/gpt-4o`/`openai/text-embedding-3-small` nodes.

**Plan:**
- Implement the baseURL match for `openrouter.ai/api/v1` in
  [nuguard/sbom/adapters/typescript/llm_clients.py](../nuguard/sbom/adapters/typescript/llm_clients.py)
  and emit `MODEL` (not `FRAMEWORK`) with `metadata.extras.gateway = "openrouter"`
  for that call site, exactly as originally planned.
- Before creating a `MODEL` node from a bare identifier match, require the
  matched "name" to resolve to a string literal (a constant/default
  parameter value) via the existing constant-resolution pass (the doc cites
  `CESScanner: resolved 2 variable(s) in ai.service.ts` as already-available
  machinery) — skip node creation when the identifier is a local variable
  reference like `model`.
- Regression tests for both: (a) `new OpenAI({ baseURL: 'https://openrouter.ai/api/v1' })`
  → single `MODEL` node, not `FRAMEWORK`; (b) a local variable named `model`
  assigned from a non-literal expression → no `MODEL` node emitted for it.

## 5. FRAMEWORK node explosion — one node per NestJS controller class (new regression)

**Root cause:** the enriched SBOM has 28 `FRAMEWORK` nodes, 26 of which are
one-per-controller (`Authcontroller`, `Blogcontroller`, `Chatcontroller`,
... `Userscontroller`), each citing the controller class's own file/line.
Ground truth models 0 `FRAMEWORK` nodes for Studyield. This looks like a
side effect introduced alongside `nestjs_adapter.py`'s endpoint extraction:
something (either the NestJS adapter itself, or a generic
class-decorator-based `FRAMEWORK` detector running independently) treats
every `@Controller`-decorated class as its own framework-component node, in
addition to correctly emitting the `API_ENDPOINT` nodes for its routes.

**Plan:**
- Locate whichever adapter/detector emits `FRAMEWORK` nodes for
  `@Controller`-decorated classes (likely a generic decorator-based
  framework detector that predates `nestjs_adapter.py` and wasn't taught to
  skip classes the new NestJS adapter already handles) and suppress it for
  classes that `nestjs_adapter.py` has already turned into `API_ENDPOINT`
  nodes — a NestJS controller is not itself a distinct framework component;
  it's the container for the endpoints already extracted.
- If a "the app uses NestJS" signal is still wanted, emit it once at the
  application level (e.g. one `FRAMEWORK` node for the `@nestjs/core`
  dependency itself, matching how other framework detections work
  elsewhere), not once per controller class.
- Regression test: an SBOM extraction fixture with 3 `@Controller` classes
  should not produce 3 `FRAMEWORK` nodes once their endpoints are captured.

## 6. Hand-rolled multi-agent orchestration still undetected

**Root cause:** the `AGENT` node present (`"Studyield Assistant"`) has
`"evidence": []`, confidence 0.44, and
`extras.source: "auto_enrichment"` with no `detection_basis` — it's a
generic LLM-generated placeholder, not a detection of the actual
`BaseAgent`/`ProblemSolverService` sequential-orchestration pattern
(`AnalysisAgent → SolverAgent → VerifierAgent → HintAgent → AlternativeMethodAgent`)
that ground truth documents with concrete AST evidence at
`base.agent.ts:27` and `problem-solver.service.ts:40`. The generic
"orchestrator class" heuristic proposed in
[studyield-sbom-fix.md](../tests/apps/studyield-app/studyield-sbom-fix.md)
item #2 was never implemented.

**Plan:**
- Implement the heuristic as originally scoped: detect an abstract/base
  class (TS) whose subclasses are all instantiated and invoked in sequence
  from one constructor/method in another class, where each call path
  reaches a recognized LLM-client call (`AiService`/anything
  `llm_clients.py` already recognizes) within a small number of hops. Emit
  one `AGENT` node named for the orchestrating service
  (`ProblemSolverService`), citing the base-class definition and the
  sequencing call site as evidence (not an empty evidence list).
- Tag it with `metadata.extras.detected_by_tiers` marking it a heuristic,
  lower-confidence detection (0.5–0.6), distinguishable from
  framework-native `AGENT` nodes — same confidence-tiering convention used
  elsewhere.
- Implement this once in a shared, language-agnostic location (e.g.
  `nuguard/sbom/adapters/generic_agent.py`) per the original plan, wired
  into both Python and TypeScript adapter registries, rather than
  duplicating per language.
- When this fires, the existing generic LLM-placeholder fallback that
  currently produces `"Studyield Assistant"` should not also fire for the
  same class — avoid emitting both a real and a placeholder `AGENT` node
  for the same orchestrator.

## 7. DEPLOYMENT still fragmented (unchanged from sbom-misses.md)

**Root cause:** unchanged since the original doc — `Ci`, `Docker`
(`start.sh:13`), `Nginx` (`frontend/Dockerfile:18`) remain 3 separate
keyword-per-node matches instead of one consolidated `docker_compose`
deployment node keyed off `docker-compose.yml`'s 6 services, as ground
truth models it.

**Plan:** carried over from sbom-misses.md §7 (already deferred there, not
re-scoped in this doc) — consolidate same-deployment keyword hits
(`docker`, `nginx`, CI-workflow-triggered-deploy) into a single node per
actual deployment target when an IaC file (`docker-compose.yml`) is present,
rather than emitting one node per keyword match across different files.

## Suggested order of work

1. **§4** (OpenRouter/MODEL guard) — smallest, most isolated, already fully
   scoped from the prior doc.
2. **§1** (`setGlobalPrefix`) — mechanical, single adapter, unblocks
   real-endpoint accuracy broadly (behavior/redteam auto-discovery, IDOR
   surface checks).
3. **§5** (FRAMEWORK-per-controller) — likely a one-line suppression once
   the emitting detector is located; highest noise-to-effort ratio.
4. **§2** (AUTH evidence ranking + Apple JWKS) — moderate, reuses an
   existing pattern (`_AUTH_STRICT_CLASSES` instantiation-over-import
   preference) from the Python adapter.
5. **§3** (TOOL: code-sandbox detection + Generic FP fix + Web Search
   evidence) — three related but separable fixes to the same adapter tier.
6. **§6** (hand-rolled AGENT detection) — largest, most novel heuristic;
   do last once the simpler evidence-ranking fixes (§2/§3) establish the
   "prefer real call site" pattern this can reuse.
7. **§7** (DEPLOYMENT consolidation) — already deferred twice; pick up only
   if time remains after 1–6.

Each fix should ship with a regression test colocated with the module it
touches (`nuguard/sbom/tests/`), plus a final re-check of
`studyield.sbom.enriched.json` against
`studyield.ground-truth.sbom.json` confirming the specific symptom in each
section above is resolved.
