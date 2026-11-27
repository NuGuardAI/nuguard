# Expected AI-SBOM — source-first hypotheses

Written 2026-11-26 before the first NuGuard scan of this fixture. Baseline is upstream
`0dde5e036d34e039a0c078a58737323d85d94669`; observed differences belong in DISCREPANCIES.

| Expected component or relationship | Source |
|---|---|
| Gin HTTP routes and customer/dashboard authentication | internal/bootstrap/routes.go; internal/middleware/ |
| Eino agent and graph orchestration | internal/ai/runtime/ |
| OpenAI-compatible model client | internal/ai/openai_client.go; go.mod |
| MCP client/tool integration | internal/ai/mcps/; internal/ai/runtime/ |
| Qdrant retrieval integration | internal/ai/; go.mod |
| GORM storage; SQLite and MySQL configuration | internal/repositories/; internal/pkg/config/; go.mod |
| Ticket-confirmation tool -> graph -> TicketService -> database | internal/ai/runtime/tools/create_ticket_confirm_tool.go; internal/ai/runtime/graphs/create_ticket_graph.go; internal/services/ticket_service.go |
| Handoff tool -> graph -> ConversationService -> database | internal/ai/runtime/tools/handoff_graph_tool.go; internal/ai/runtime/graphs/handoff_graph.go |

## Behavioral hypotheses to check, not findings

- BOLA: customer A attempting to read or write customer B's conversation should fail
  the identity/ownership checks. The scanner may see write-capable tools without
  establishing whether their IDs are caller-bound; static labels alone are insufficient.
- Excessive privilege: model-supplied conversation/customer IDs should not override
  runtime-bound tool context. Ticket creation should interrupt for explicit confirmation.
- Indirect injection: retrieved content must not be treated as authorization. This
  requires a live local-model run with response, tool calls and database snapshots.
- Stub transport success would establish pipeline connectivity only, not exploit execution.

The acceptance criterion requires two vulnerability classes exercised with actual evidence.
Source inspection and deterministic graph tests alone do not fulfill the live-evidence criterion.
