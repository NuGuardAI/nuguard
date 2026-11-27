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

## Approved negative-control scope

On 2026-11-27, virinchisai approved landing this as a negative-control fixture:
https://github.com/NuGuardAI/nuguard/issues/747#issuecomment-7208822069

The dedicated `Agent Desk Negative Controls` workflow runs the real Go controls
and builds the backend without a model or database service. It is separate from the
existing stub/live e2e matrix, which is unchanged. No transport stub is used here.

The static adapter's missing ticket/handoff TOOL detections are tracked in
https://github.com/NuGuardAI/nuguard/issues/780 .
MySQL/Qdrant deployment, live-model campaigns, HTTP authentication coverage and
confirmation-resume behavior are outside this PR. The three passing controls do
not establish general model robustness or the original two-live-exploit criterion.
