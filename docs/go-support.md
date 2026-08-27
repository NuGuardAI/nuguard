# Go Backend Discovery — Findings & Improvement Plan

**Status: all 8 phases complete.** Go backends now get file/line-anchored
FRAMEWORK, API_ENDPOINT, MODEL, DATASTORE, AUTH, PROMPT, AGENT, TOOL, and
MCP_SERVER nodes from real structural/AST evidence instead of bare
whole-file regex matches — validated against both fixture apps and the
live `mosaic-care/healthcare-service` repo (phases 5, 6, 8). Deferred items
(gqlgen resolver extraction, langchaingo TOOL/agent extraction, Go
function/tool schema detection) share one root cause — `go_parser` doesn't
parse function *declarations*, only imports/instantiations/calls/string
literals — and are tracked as a single follow-up rather than three
separate ones. See the phase sections below for what shipped, what was
explicitly scoped out and why, and the real-world validation numbers for
each.

## Finding: Go backends are not being structurally discovered

Confirmed by inspecting `tests/apps/kscope/kscope.sbom.json` (SBOM generated
for `mosaic-care/healthcare-service`, a Go backend) and the SBOM extractor
source.

- Every node in the SBOM comes from either regex-based generic adapters
  (`model_generic`, `auth_jwt`, `datastore_generic`, `tool_generic`, …) or
  IaC/Dockerfile scanning. **Zero** nodes have file/line evidence tied to
  `.go` source, and `frameworks: []` / `api_endpoints: []` are empty despite
  this clearly being a Go backend (`Golang:1.26` container image,
  `resolver_functional_suite_test.go` referencing a GraphQL resolver test,
  521K LOC).
- Root cause, in `nuguard/sbom/adapters/registry.py::default_framework_adapters()`:
  this is the AST-aware adapter list that drives high-confidence,
  file/line-anchored detection, and it **only imports Python and TypeScript
  adapters**. `nuguard/sbom/adapters/go/` exists (`GoFrameworkAdapter` base
  class + `MCPGoServerAdapter`) but is never imported/registered here — dead
  code except for its own unit tests.
- `nuguard/sbom/core/go_parser.py` is a full 1,379-line tree-sitter-based Go
  parser (imports, instantiations, function calls) — also built but unused in
  the actual scan pipeline (`extractor/core.py` has no `.go`-specific
  dispatch at all; `.go` files fall through to the generic regex-only path).
- `nuguard/sbom/core/route_patterns.py` (drives both the `api_endpoint_generic`
  regex adapter and the summary's `api_endpoints`) only matches Python
  decorator syntax (`@app.get(...)`) and lowercase Express-style calls
  (`app.get(...)`/`router.post(...)`). Go router idioms use **uppercase**
  methods (`r.GET(...)`, `e.POST(...)`) and different variable names (`r`,
  `e`, `mux`), so they never match — and GraphQL (gqlgen, the likely
  framework here given the resolver test file) isn't route-based at all and
  needs its own extraction path.

Net effect: for a Go backend, NuGuard degrades to keyword-spotting across raw
text — no AGENT/TOOL/MODEL/API_ENDPOINT node gets real provenance, no
FRAMEWORK node is ever emitted, and endpoints are invisible to
`analyze`/`behavior`/`redteam` downstream.

## Plan

### Phase 1 — wire up the parser (foundation, unblocks everything else)
1. Route `.go` files through `go_parser.parse_go()` in `extractor/core.py`,
   mirroring how `.py`/`.ts` get AST `parse_result` before adapter dispatch.
2. Register the existing `go/` adapters in `default_framework_adapters()`
   (currently just `MCPGoServerAdapter`) so Go gets a real dispatch path at
   all.

### Phase 2 — Go web framework adapters ✅ done
(Biggest gap: zero API_ENDPOINT/FRAMEWORK coverage.)
3. ✅ Added adapters for the common Go HTTP routers: `net/http`,
   `gin-gonic/gin`, `labstack/echo`, `go-chi/chi`, `gorilla/mux` — emit
   FRAMEWORK + API_ENDPOINT nodes with method/path/file/line, using
   `go_parser`'s function-call extraction (not new regex). Verified
   end-to-end against fixtures for all five, including gorilla/mux's
   chained `.HandleFunc(...).Methods("GET")`.
4. ✅ Added a `gqlgen` adapter — emits a FRAMEWORK node (`api_style:
   graphql`) when `github.com/99designs/gqlgen` is imported.
   **Scoped down from the original plan**: resolver-level operation
   extraction (mapping `queryResolver`/`mutationResolver` methods to
   individual GraphQL operations — the GraphQL analogue of per-route
   API_ENDPOINT nodes) needs Go function-*declaration* parsing, which
   `go_parser.GoParseResult` doesn't have yet (it extracts imports,
   instantiations, calls, and string literals, but not declarations).
   Follow-up: extend `go_parser` with a function-declaration pass, then
   revisit gqlgen resolver extraction and `.graphqls`/`.graphql` schema
   file parsing (schema files aren't `.go`, so they also need a new
   dispatch branch in `extractor/core.py`, analogous to the SQL/YAML/JSON
   branches).

### Phase 3 — Go AI/LLM SDK adapters ✅ done
(Previously only generic regex caught model names, with no file/line
evidence and no FRAMEWORK node.)
5. ✅ Added adapters for `langchaingo`, `go-openai` (sashabaranov),
   `anthropic-sdk-go`, `google/generative-ai-go` — FRAMEWORK + MODEL nodes
   with construction-site (`ast_call`/`ast_instantiation`) evidence instead
   of bare string matches:
   - `google/generative-ai-go`: reads the model string straight from
     `client.GenerativeModel("gemini-...")` calls.
   - `go-openai` / `anthropic-sdk-go`: read the `Model` field off request
     struct literals (`ChatCompletionRequest{Model: "..."}`,
     `MessageNewParams{Model: "..."}`). SDK-provided constants
     (`openai.GPT4`, `anthropic.ModelClaude3_5SonnetLatest`) are qualified
     identifiers `go_parser` can't resolve to a literal, so those requests
     still get the FRAMEWORK node but no MODEL node — acceptable, matches
     how unresolved values are already treated elsewhere in the Go adapters.
   - `langchaingo`: one FRAMEWORK-import gate covers all `llms/*` provider
     subpackages, and a single `WithModel("...")` call-name scan covers
     model selection across every provider without per-provider branching
     (langchaingo's functional-options pattern is provider-agnostic here).
   - TOOL/agent extraction for langchaingo (`tools.Tool` implementations,
     `agents.Executor` construction) is out of scope — it needs
     interface-implementation analysis `go_parser` doesn't do; noted as a
     further follow-up alongside the gqlgen resolver-extraction gap.

### Phase 4 — datastore/auth structural detection ✅ done
6. ✅ Added `GoDatastoreAdapter` (`mongo-driver`'s `mongo.Connect`/
   `mongo.NewClient`, `redis/go-redis`'s `redis.NewClient`, and stdlib
   `database/sql`'s `sql.Open(driverName, dsn)` with driver→provider
   resolution) so DATASTORE nodes get real call-site evidence instead of
   relying solely on IaC/compose files. One node per distinct provider per
   file, reusing the Python adapter's `datastore:{provider}` canonical-name
   convention.
7. ✅ Added `GoJWTAdapter` (`golang-jwt/jwt`, any major version, plus the
   legacy `dgrijalva/jwt-go` fork — `jwt.NewWithClaims`/`Parse`/
   `ParseWithClaims`) and `GoOAuth2Adapter` (`golang.org/x/oauth2` —
   `oauth2.Config{...}` or `oauth2.NewClient(...)`). Both reuse the exact
   `auth:jwt`/`auth:oauth2` canonical names the generic regex fallback
   already uses, so a structural hit and a same-file text hit merge into
   one node with upgraded evidence rather than producing a duplicate.
   Verified end-to-end: mixing regex-only and structural Go source in one
   file still yields exactly one `AUTH | JWT` node.

   **Known limitation, not addressed here**: receiver/package-name matching
   in both adapters assumes the default (unaliased) import name — e.g.
   `jwt.NewWithClaims(...)`. A file that imports under an alias
   (`import j "github.com/golang-jwt/jwt/v5"`) won't be picked up by the
   structural adapter (the bare-word regex fallback still catches it,
   just without call-site evidence). `mcp_server.py`'s
   `_package_aliases()` shows the pattern to fix this properly; not worth
   promoting to the shared base for a phase-4 pass given aliased imports
   are the uncommon case.

### Phase 5 — validation
8. ✅ Added a fixture-based test app,
   `nuguard/sbom/tests/fixtures/apps/go_healthcare_service/` — a small
   gin + go-openai + MongoDB + JWT backend modeled on
   `mosaic-care/healthcare-service`'s actual shape (the exact combination
   that produced zero file-anchored nodes before phases 2-4). The
   integration test in `nuguard/sbom/tests/test_go_healthcare_fixture.py`
   asserts FRAMEWORK (gin, go-openai), API_ENDPOINT (both gin routes),
   MODEL (`gpt-4-turbo` from the request struct), DATASTORE (mongodb), and
   AUTH (jwt) nodes are all present and that every one of them carries
   `Evidence` with a real `.go` file path and a positive line number —
   mirroring the existing Python/TS adapter integration tests
   (`test_milo_style.py`'s pattern). 6/6 pass.
9. ✅ **Unblocked and run** — repo access was restored (the PAT in
   `tests/apps/kscope/.env` was refreshed) and `kscope-test.sh` was re-run
   against the real `mosaic-care/healthcare-service` repo (830 `.go`
   files). Before → after: `FRAMEWORK` 0 → 3 (Gin, Gorilla/Mux, Net/HTTP),
   `API_ENDPOINT` 0 → 34.

   Cross-checked the regenerated SBOM against the actual source (separate
   clone, manual grep):
   - **net/http** (32 endpoints): every path matches the 33 real
     `http.HandleFunc`/`http.Handle` call sites in
     `backend/cmd/healthrecord_repository/main.go` 1:1 (33 calls → 32
     unique paths after `/` is registered twice and correctly dedupes to
     one node). Zero false positives, zero misses.
   - **gin** (2 endpoints: `GET /`, `POST /`): gin is only used inside an
     internal vendored library (`internal/mongolib/mserver`), which
     registers 9 routes, 7 with empty-string paths on a router group
     (`rcBase.GET("", ...)`) that need prefix composition this pass
     doesn't attempt — the same documented limitation Python's FastAPI
     adapter already has for router-group roots. The 2 detected are
     exactly the 2 with a real absolute path.
   - **gorilla/mux** (0 endpoints, FRAMEWORK node still emitted):
     confirmed correct — the only file importing it
     (`internal/msutil/handler/blob.go`) uses it solely for
     `mux.Vars(r)`, never for route registration.
   - **MongoDB** (1 DATASTORE node): correctly detected with structural
     evidence, matching the app's real `mongo.Connect(...)` calls; no
     Redis or `database/sql` exist in the real backend, so their absence
     is correct, not a miss.
   - `policy compile` then failed on an unrelated pre-existing issue
     (`cognitive_policy.md` not found) — nothing to do with the Go work.

   **Gaps found during this cross-check, scoped as phases 6-8 below**:
   a genuine ~60-line safety-critical system prompt
   (`backend/chat/chat.go`'s `const systemPrompt`) gets no real evidence
   (phase 6); the app's primary LLM integration is a hand-rolled HTTP
   client calling `api.anthropic.com` directly rather than
   `anthropic-sdk-go`, so it's invisible to any SDK adapter (phase 8); and
   a `Snowflake` DATASTORE false positive was found, sourced from the
   literal string `"Snowflake"` inside a bundled frontend icon library
   file (`lucide-react.js`) — pre-existing regex-fallback behavior, not
   introduced by or fixable within this Go-adapter work.

Phase 1+2 give the highest ROI — they turn "framework: none detected" into
real coverage and unblock endpoint-level `analyze`/`redteam` targeting,
which is currently completely blind for Go backends.

### Phase 6 — Go prompt-constant extraction ✅ done
Ground-truth cross-check (phase 5 item 9) found a real, large,
safety-critical system prompt —
`backend/chat/chat.go:48: const systemPrompt = \`You are Mosaic's health
assistant...\`` (~60 lines, self-harm/escalation handling) — and a second
one in `backend/ingest/supplement.go` (`const supplementSysPrompt`) — that
the SBOM represents as a single generic, content-free `prompt_generic`
node (bare keyword match, confidence 0.48). This is exactly the kind of
thing `behavior`/`redteam`'s policy-alignment and jailbreak-surface checks
need to inspect, and right now it's invisible.

10. ✅ Added `extract_go_prompt_constants()`
    (`nuguard/sbom/adapters/go/prompts.py`), mirroring Python's
    `_extract_python_prompt_constants`: scans `GoParseResult.string_literals`
    for package-level (`lit.context is None` — not inside a
    function/method) `const`/`var` string declarations whose assigned
    name ends in `...Prompt`/`...prompt` (case-insensitive suffix match,
    not Python's `(?:^|_)PROMPT$` anchor — Go's camelCase convention
    means `systemPrompt`/`supplementSysPrompt` don't have the
    underscore Python's SCREAMING_SNAKE_CASE names do) and whose content
    is >=80 chars. Emits a PROMPT node per match with the full content
    in `metadata.extras.content` and real file/line evidence
    (`evidence_kind: "ast_constant"`). Reuses
    `GoFrameworkAdapter._template_vars()` for `{{.Name}}`/`{name}`
    template-variable extraction rather than writing a second regex.
    Called unconditionally on every `.go` file's parse result in
    `extractor/core.py`'s Go dispatch branch, regardless of which (if
    any) framework adapters matched — mirroring Python's "Phase
    1a-prime" placement.
11. Deferred: prompts built from a Go string-formatting call
    (`fmt.Sprintf("You are %s...", ...)`) assigned to a prompt-like
    variable name — not found in the kscope ground truth (all real
    prompts there are raw-string constants), so not implemented; the
    `GoFunctionCall` data needed for it already exists if a future repo
    needs it.
12. ✅ Extended `go_healthcare_service`'s `triage.go` with a package-level
    `const systemPrompt` (modeled on the real one) and added
    `TestPromptDetection` to `test_go_healthcare_fixture.py`, asserting a
    PROMPT node with real content and evidence. Also added
    `test_go_prompt_constants.py` (7 unit tests: package-level detection,
    camelCase suffix matching, function-local strings correctly
    excluded, min-length and eval/test skip-word filtering, template-variable
    capture). 7 + 7 = 14 new tests, full `nuguard/sbom/` suite (1222
    tests) + ruff + mypy all pass.

    **Real-world validation** — re-ran `kscope-test.sh` against the live
    `mosaic-care/healthcare-service` repo: `PROMPT` node count went from
    1 (the old content-free generic hit) to 9 — 8 real prompt constants
    with full content now captured (`Systemprompt`: 9506 chars,
    `backend/chat/chat.go:48`, content verified byte-for-byte against the
    real prompt's opening line; `Supplementsysprompt`: 1701 chars,
    `backend/ingest/supplement.go`; plus 6 more across
    `classify`/`fhir`/`filter`/`genomic`/`protocol`) — alongside the one
    pre-existing generic hit still present for whatever didn't match the
    structural adapter's heuristics.

### Phase 7 — Go agent/orchestration framework adapters (Python parity) — 13/14 done, 15/16 deferred
Even after phases 1-6, Go's adapter roster stays far short of Python's 22
and TypeScript's 14: Python has structural adapters for LangGraph,
CrewAI, AutoGen, Agno, Azure AI Agents, Bedrock AgentCore, Google ADK,
Semantic Kernel, LlamaIndex, OpenAI Agents SDK, Guardrails AI (+
heuristic), an MCP *client* adapter, and OpenAI function-schema
detection — Go has none of these agent/orchestration/guardrail
equivalents (only `mcp-go` for MCP *servers*, from before this
investigation). This phase is about closing that parity gap for the Go
frameworks that actually exist in that space, not inventing Go versions
of Python-only frameworks.

13. ✅ **Agent/orchestration frameworks** — the two real Go options as of
    this writing. Neither is present in any locally ground-truth-validated
    fixture (kscope doesn't use them), so call shapes were verified
    against upstream source/docs via GitHub/pkg.go.dev before writing the
    adapters, rather than guessed:
    - `EinoAdapter` (`cloudwego/eino`, ByteDance's Go LLM application
      framework — graph-based orchestration, roughly LangGraph's Go
      analogue). `compose.NewChain[I, O](...)`/`compose.NewGraph[I, O](...)`
      → AGENT node — the `[I, O]` generic type args are already stripped
      by `go_parser._split_callee` before receiver/function-name
      splitting, so no special handling was needed there.
      `utils.NewTool[...](toolInfo, invokeFunc)` → TOOL node, reading
      `Name`/`Desc` off the `toolInfo` argument — which resolves through
      `go_parser`'s existing single-file symbol table whether the
      `&schema.ToolInfo{...}` literal is inline or assigned to a variable
      first (verified with a dedicated test); a `toolInfo` declared in a
      *different* file doesn't resolve and is silently skipped, same as
      every other unresolved-value case in the Go adapters.
    - `GenkitGoAdapter` (`firebase/genkit/go`, Google's Genkit Go SDK).
      `genkit.DefineFlow(g, "name", ...)` → AGENT node;
      `genkit.DefineTool(g, "name", "desc", ...)` → TOOL node with the
      description captured too. `genkit.DefinePrompt(g, "name",
      ai.WithPrompt("..."), ...)` is **not** covered — the prompt text is
      the argument to a *nested* call (`ai.WithPrompt(...)`), which
      `go_parser._extract_value` has no case for (falls through to an
      unresolvable `$...` catch-all) — not worth a dedicated nested-call
      unwrap for a framework with no local ground truth yet.
14. ✅ **MCP client adapter** (`MCPGoClientAdapter`,
    `github.com/mark3labs/mcp-go/client`) — mirrors Python's
    `mcp_client.py` closely: one document-scoped MCP_SERVER node (fixed
    canonical name `mcp_client:servers`, identical to the Python
    adapter's, so a mixed Python+Go app still merges into a single node)
    plus one TOOL node per client-construction call site
    (`NewStdioMCPClient`/`NewStdioMCPClientWithOptions`/`NewSSEMCPClient`/
    `NewStreamableHttpClient`/`NewOAuthSSEClient`/
    `NewOAuthStreamableHttpClient`/`NewClient`, verified against
    `pkg.go.dev/github.com/mark3labs/mcp-go/client`'s full constructor
    list), tagged `trust_level="untrusted"` and `mcp_server_url=...` (the
    exact `NodeMetadata` fields
    `redteam/scenarios/generator.py`'s `_mcp_toxic_flow_scenarios`/
    `_mcp_attack_scenarios` filter on) — so this wires straight into
    MCP-toxic-flow scenario generation with no redteam-side changes.
    `NewInProcessClient`/`NewInProcessClientWithSamplingHandler` are
    deliberately excluded: they wrap a local, developer-controlled
    `*server.MCPServer` value, not a user-configured/untrusted endpoint,
    so they don't fit this adapter's threat model (covered by a
    dedicated negative test).

    Complements the existing server-side `MCPGoServerAdapter` from
    before this investigation (both can fire in the same file for a
    proxy/gateway app that is simultaneously an MCP server and client).

    **Tests**: `test_go_agent_framework_adapters.py`, 11 unit tests
    covering eino (chain/graph construction, tool name/desc resolution,
    unresolvable-info skip, no-import no-op), genkit-go (flow/tool
    detection, no-import no-op), and the MCP client adapter (stdio,
    SSE with URL resolution, in-process exclusion, no-import no-op). Full
    `nuguard/sbom/` suite (1233 tests) + ruff + mypy all pass.
    Re-ran `kscope-test.sh` against the real repo to confirm no
    regression/crash — node counts identical to the phase-6 run, as
    expected since kscope uses none of these three frameworks.
15. ⏸ **Deferred** — **Guardrails / validation**: no dominant Go equivalent of
    `guardrails-ai` exists yet; track this one rather than build
    speculatively. If a real guardrails library shows up in a scanned
    repo, that's the trigger to add it, not before.
16. ⏸ **Deferred** — **Function/tool schema detection**: Go doesn't have Python's
    duck-typed docstring-to-schema convention, but `langchaingo`'s
    `tools.Tool` interface (`Name()`, `Description()`, `Call(...)`
    methods) and eino's tool-definition structs are the Go analogues of
    Python's `OpenAIFunctionSchemaAdapter`. Needs the same
    function-*declaration* parsing gap noted in phase 2 (gqlgen) and
    phase 3 (langchaingo TOOL/agent extraction) — this is the third
    phase blocked on that same `go_parser` extension, which is reason
    enough to schedule it as dedicated `go_parser` work rather than
    deferring it a third time piecemeal.

### Phase 8 — direct-HTTP LLM call detection ✅ done
Ground-truth cross-check (phase 5 item 9) found kscope's actual primary
LLM integration doesn't use any SDK at all: `backend/chat/chat.go` hand-
rolls an HTTP client (`anthropicReq`/`anthropicResp` structs,
`http.NewRequest(http.MethodPost, "https://api.anthropic.com/v1/messages", ...)`)
with the model name in a plain `const chatModel = "claude-sonnet-4-6"`.
None of the phase-3 SDK adapters can see this — it's real, structural,
first-party LLM usage that's currently only caught by the low-confidence
generic `model_generic` regex (0.53 confidence, no call-site evidence,
and prone to false positives like the `o0`-`o7` matches also found in
this SBOM).

17. ✅ Added `extract_go_direct_http_llm_calls()`
    (`nuguard/sbom/adapters/go/direct_http_llm.py`), keyed on well-known
    LLM API hostnames in string literals (`api.anthropic.com`,
    `api.openai.com`, `generativelanguage.googleapis.com`, + 8 more —
    a superset of Python's proxy-pattern table since native provider
    hosts need to be included here, unlike the Python/TS tables which
    only resolve an *OpenAI-compatible client's* `base_url` override) —
    a plain function, not a `GoFrameworkAdapter`, since there's no import
    to gate on. On a hostname match, walks the same file's other string
    literals for a value matching `MODEL_NAME_PATTERNS` — the *exact*
    pattern set the `model_generic` regex adapter uses (extracted to a
    named, importable constant in `adapters/registry.py`; the
    `RegexAdapter(...)` call now references it directly instead of
    inlining the four patterns — a pure extraction, verified
    behavior-identical by the full suite before adding any new code) —
    and emits a MODEL node anchored to the *model string's own* file/line,
    not wherever `model_generic`'s whole-file sweep happens to land.
18. ✅ Checked and **not factored into a shared cross-language helper**:
    TypeScript's `llm_clients.py` already duplicates Python's
    `_BASE_URL_TO_PROVIDER` table independently (with a "kept in sync"
    comment) rather than importing a shared one — that's this codebase's
    established, working convention for this exact kind of table, so Go
    follows it instead of introducing a third pattern. The
    `MODEL_NAME_PATTERNS` regex set (item 17) *was* factored into a real
    shared constant, because that pattern set needs byte-identical
    behavior between `model_generic` and the new Go function, not just
    "the same design intent" the per-language provider tables need.
19. ✅ Extended `go_healthcare_service` with `chat.go` — a hand-rolled
    HTTP client hitting `api.anthropic.com` directly (mirroring the real
    `backend/chat/chat.go`, no `anthropic-sdk-go` import) — and added
    `TestModelDetection.test_direct_http_anthropic_call_model_extracted_with_evidence`
    to `test_go_healthcare_fixture.py`. Also added
    `test_go_direct_http_llm.py` (5 unit tests: hostname+model
    co-occurrence required, host-without-model and model-without-host
    both correctly yield nothing, multiple models in one file each get
    their own node). 5 + 1 = 6 new tests, full `nuguard/sbom/` suite
    (1239 tests) + ruff + mypy all pass.

    **Real-world validation** — re-ran `kscope-test.sh` against the live
    repo: `claude-sonnet-4-6` (`backend/chat/chat.go:44`) and
    `claude-opus-4-8` (`backend/ingest/extract.go:26`) both flipped from
    `model_generic` (confidence 0.44-0.53, no evidence) to
    `go_direct_http_llm` (confidence 0.952, exact file/line evidence at
    the `const` declaration). `claude-haiku-4-5` stayed on
    `model_generic` — its declaring file (`backend/ingest/classify.go`)
    has no LLM-hostname string literal of its own (the URL constant it
    uses lives in a different file; this adapter is file-scoped, matching
    every other Go adapter's fixture-app single-file basis) — a real,
    documented boundary, not a bug. Confirmed in passing that the
    remaining `o0`-`o7` false positives are unrelated to Go entirely —
    `frontend/webapp/.vite/deps/echarts.js`, a bundled frontend file, not
    backend source.

## Summary

`mosaic-care/healthcare-service` node counts, before this investigation
started → after all 8 phases:

| Component type | Before | After |
|---|---|---|
| FRAMEWORK | 0 | 3 (Gin, Gorilla/Mux, Net/HTTP) |
| API_ENDPOINT | 0 | 34, all cross-checked against real call sites |
| PROMPT | 1 (content-free) | 9 (8 with full content, real evidence) |
| MODEL (structural, high-confidence) | 0 | 2 (was: all 11 on low-confidence bare regex) |
| DATASTORE (structural) | 0 | 1 (MongoDB) |
| AUTH (structural) | 0 | 1 (JWT) |

New adapter files (16, all under `nuguard/sbom/adapters/go/`): the
existing `mcp_server.py` plus `http_router.py` (gin/echo/chi),
`net_http.py`, `gorilla_mux.py`, `gqlgen.py`, `langchaingo.py`,
`go_openai.py`, `anthropic_sdk.py`, `google_genai.py`, `datastores.py`,
`auth.py`, `prompts.py`, `eino.py`, `genkit.py`, `mcp_client.py`,
`direct_http_llm.py`. Plus the `go_parser.py` dispatch wiring in
`extractor/core.py` (phase 1) and the `MODEL_NAME_PATTERNS` extraction in
`adapters/registry.py` (phase 8).

Test coverage: `nuguard/sbom/tests/test_go_*.py` (10 files, 98 tests
total, including the pre-existing `go_parser`/MCP-server tests), covering
phases 1-8 with real file/line evidence assertions — plus the
`go_healthcare_service` fixture app and its integration test
(`test_go_healthcare_fixture.py`). Full `nuguard/sbom/` suite: 1239 tests
passing.

Remaining deferred work (not blocking, tracked here for whoever picks
this up next): gqlgen resolver-level GraphQL operation extraction (phase
2), langchaingo TOOL/agent extraction (phase 3), and Go
function/tool-schema detection (phase 7 item 16) all need the same
`go_parser` extension — a function-*declaration* parsing pass, which
doesn't exist yet (`GoParseResult` currently covers imports,
instantiations, calls, and string literals only). That's the single
highest-leverage next investment for Go SBOM accuracy. Guardrails/
validation detection (phase 7 item 15) has no dominant Go library to
build against yet and should stay untracked until one appears in a
scanned repo.
