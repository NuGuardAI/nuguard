# Authorization feasibility assessment — 2026-11-26

**No exploitable vulnerability is established by this assessment.** Three focused
regression tests exercise real pinned Go implementation code using isolated SQLite
and direct service/graph calls. No HTTP requests, model responses or model-selected
tool calls were recorded. Do not describe these tests as live-agent or e2e results.

| Probe | Actual observation | Evidence |
|---|---|---|
| Customer isolation with legitimate control | Caller customer 1 is accepted for its conversation and rejected for customer 2's; message count remains 0 in both validation probes | `TestNuGuardCustomerCannotSendToAnotherConversation` |
| Model-supplied object IDs | Arguments requesting conversation 202 retain runtime-bound conversation 101 | `TestNuGuardTicketArgumentsCannotOverrideConversation` |
| Write without confirmation | `confirmed:true` in tool arguments does not bypass the graph interrupt; response is empty and ticket count stays 0 -> 0 | `TestNuGuardTicketRequiresConfirmationWithoutDatabaseWrite` |

Exact run output: `evidence/negative-controls.txt`.

## Reproduce without a model

From this directory, using Go 1.26.11 or a compatible toolchain:

```sh
go test ./internal/ai/runtime/graphs -run TestNuGuard -v -count=1
go build -o /tmp/nuguard-agent-desk-server ./cmd/server
```

All data is synthetic. The tests do not disable, bypass or remove upstream
ownership and confirmation checks. The first probe invokes sender validation;
it does not claim to prove the entire HTTP authentication or message-send pipeline.
The third exercises the initial graph call, not confirmation resume behavior.

## Source trace

- Customer GET/list handlers call `IsCustomerConversationOwner`; message sends use
  `ValidateConversationSender`, which rejects another customer's conversation.
- `CreateTicketGraphTool.Build` takes the conversation from runtime context.
  Its schema exposes title/description, not conversation/customer IDs.
- `buildCreateRequest` binds the conversation before parsing title/description.
- `Run` interrupts before calling `TicketService.CreateFromConversation`; the
  service derives the ticket customer from that conversation.
- Handoff uses the same runtime-bound conversation. Its off-hours path deliberately
  sends a notice without confirmation and is covered by an upstream test. That
  documented path alone is not evidence of excessive privilege.

## Acceptance blockers and next bounded decision

1. The requested two-class **live** evidence is not available. Deterministic controls
   support the tested boundaries; they cannot establish exploitability or model robustness.
2. The static adapter detects no registered TOOL nodes; see DISCREPANCIES.
3. This machine has no available Docker runtime or local inference server, so the
   MySQL/Qdrant lifecycle and live-agent runs have not been validated. No paid
   endpoint was used as a substitute.
4. e2e matrix and prepublish integration have deliberately not been added yet:
   a transport stub that never calls tools would not resolve these blockers.

Next maintainer decision: accept this as a protected negative-control fixture and
approve a narrowly scoped Go registry-adapter follow-up, or identify an intended
upstream agent path for the requested live two-class exercise. Do not remove existing
checks just to obtain a finding. This first fixture does not close #747.
