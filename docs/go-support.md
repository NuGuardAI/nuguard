# Go Backend Discovery — Findings & Improvement Plan

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

### Phase 4 — datastore/auth structural detection
6. Add a Go datastore adapter (`mongo-driver`, `database/sql` + driver
   imports, `redis/go-redis`) so DATASTORE nodes get code evidence instead
   of relying solely on IaC/compose files.
7. Extend auth detection with Go-specific JWT/OAuth2 libraries
   (`golang-jwt/jwt`, `golang.org/x/oauth2`) as structural adapters,
   replacing the current bare-word regex matches.

### Phase 5 — validation
8. Add a fixture-based test app (small Go+gin or Go+gqlgen service) under
   `tests/apps/` or `nuguard/sbom/tests/fixtures/` and assert
   FRAMEWORK/API_ENDPOINT/MODEL nodes get proper file:line evidence —
   mirroring existing Python/TS adapter tests.
9. Re-run `tests/apps/kscope/kscope-test.sh` once the token/repo access
   issue for `mosaic-care/healthcare-service` is resolved, and diff the SBOM
   before/after to confirm `frameworks` and `api_endpoints` are populated.

Phase 1+2 give the highest ROI — they turn "framework: none detected" into
real coverage and unblock endpoint-level `analyze`/`redteam` targeting,
which is currently completely blind for Go backends.
