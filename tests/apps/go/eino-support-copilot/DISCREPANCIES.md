# Discrepancies: expected vs generated AI-SBOM

Compared `EXPECTED_SBOM.md` (committed before any NuGuard run) with the output of
`nuguard sbom generate --config nuguard.prepublish.yaml` (deterministic, no LLM) on this directory.
NuGuard 0.9.16, `develop` at `81ac4b93`, 2026-11-01.
Result: 33 nodes, 22 edges (1 AGENT, 2 API_ENDPOINT, 1 AUTH, 6 CONTAINER_IMAGE, 1 DATASTORE,
5 DEPLOYMENT, 3 FRAMEWORK, 1 MCP_SERVER, 2 MODEL, 3 PRIVILEGE, 2 PROMPT, 6 TOOL); 145 Go module dependencies.

## Matches
- Frameworks: `net/http`, Eino, MCP Go (`mark3labs/mcp-go`).
- All Go modules from `go.mod`, including the Eino modules marked `// indirect`, eino-ext, mcp-go, pgx and golang-jwt.
- All 4 MCP tools from `server.AddTool(mcp.NewTool(...))`: `get_account_status`, `get_payment_status`,
  `get_service_status`, `refund_payment`.
- The MCP endpoint `/mcp` and the MCP client (`client.NewStreamableHttpClient`).
- AUTH `JWT` from `auth.go` (golang-jwt), with `PROTECTS` edges.
- System prompt `const systemPrompt` in `internal/agent/prompt.go`.
- Postgres datastore; Dockerfiles and compose services.

## Discrepancies

| # | Expected | Found | Evidence | Class |
|---|----------|-------|----------|-------|
| D1 | 17 routes registered as Go 1.22 method patterns (`mux.HandleFunc("POST /api/v1/conversations", ...)`, `"POST /chat"`, `"POST /auth/token"`, ...) | Only the catch-all `ANY /` (the pattern without a method) and `ANY /mcp` | `server.go:88`; no node for lines 62-86 | NuGuard detection gap |
| D2 | `/chat` and `/api/v1/*` require a JWT | `auth_required: false` on both endpoints, although AUTH `JWT` has `PROTECTS` edges to them | endpoint metadata vs edges | NuGuard detection gap |
| D3 | One ADK `ChatModelAgent` (`adk.NewChatModelAgent`) run by `adk.NewRunner` | 1 synthesized agent "Eino Support Copilot Assistant" with no evidence and no edges | AGENT node | NuGuard detection gap (Eino ADK not covered) |
| D4 | Agent calls the 4 MCP tools; MCP server implements them | `Eino CALLS` each tool; tools `CALLS` a generic "User-Configured MCP Servers" node, not the in-repo `cmd/mcp-server` | edge list | NuGuard detection gap |
| D5 | `refund_payment` marked high privilege (it writes payments and is annotated `WithDestructiveHintAnnotation(true)`) | `high_privilege: false` on all tools | tool metadata | NuGuard detection gap |
| D6 | Model from env `MODEL_NAME` (`gpt-5.4-mini` in CI) via `openai.NewChatModel` | `gpt-4.1-mini` (compose default) and `gpt-5.4-mini` (a comment in `scripts/serve.sh`); no node from the eino-ext model constructor | MODEL evidence | NuGuard detection gap |
| D7 | Prompts: system prompt and repair prompt | Also a PROMPT `Generic` from `fixture/nuguard_seed.sql:70`, which is the injected incident text (data, not a prompt) | PROMPT evidence | False positive (but it does point at the injection payload) |
| D8 | Datastore found from code (`pgxpool`, goose migrations, pgvector) | Postgres found only from `scripts/serve.sh`; no vector store | DATASTORE evidence | NuGuard detection gap |
| D9 | Only application code | A second `MCP Client (NewStreamableHttpClient)` TOOL from `internal/mcpserver/server_test.go`; PRIVILEGE `Filesystem Write` from `knowledge_handler_test.go` | node evidence | NuGuard detection gap (test files scanned) |
| D10 | No code execution | PRIVILEGE `Code Execution` at `internal/runtrace/repository.go:159` (`r.db.Exec(...)`, a SQL update) | PRIVILEGE evidence | False positive |
| D11 | MCP server endpoint has no auth | Correctly `auth_required: false` on `/mcp` | | Match (risk: anything that reaches it can call `refund_payment`) |

## NuGuard bug found by this fixture

`nuguard behavior` crashed on this app (`1 validation error for TurnRecord canary_hits.0 Input should
be a valid string`) and reported `scenarios_executed: 0`. The behavior runner stored the tool calls that
`TargetAppClient.send()` returns as `TurnRecord.canary_hits: list[str]`; this app's `/chat` returns
`tool_calls`, which no earlier fixture did. Fixed separately (branch
`fix/behavior-tool-calls-not-canary`); with the fix, behavior executes normally (1 scenario, 8 turns).
