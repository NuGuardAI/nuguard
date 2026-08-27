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
9. ⏸ **Blocked** — re-running `tests/apps/kscope/kscope-test.sh` against
   the real `mosaic-care/healthcare-service` repo to diff the before/after
   SBOM needs GitHub access to that repo, which is still unavailable: `gh
   api repos/mosaic-care/healthcare-service` returns `404 Not Found` under
   the authenticated account, the same result found at the start of this
   investigation. The fixture-based validation in #8 exercises the same
   code paths against a repo-shaped fixture instead; re-run this item once
   repo access is granted.

Phase 1+2 give the highest ROI — they turn "framework: none detected" into
real coverage and unblock endpoint-level `analyze`/`redteam` targeting,
which is currently completely blind for Go backends.
