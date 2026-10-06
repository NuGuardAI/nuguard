# Discrepancies: expected vs generated AI-SBOM

Compared `EXPECTED_SBOM.md` (committed before any NuGuard run) with the output of
`nuguard sbom generate --no-llm` on this directory. NuGuard: upstream develop at 8b32b75f.
Result: 20 nodes, 40 edges (9 AGENT, 1 API_ENDPOINT, 1 DATASTORE, 2 FRAMEWORK, 5 PROMPT, 2 TOOL).

## Matches
- Frameworks: Spring Boot, LangChain4j.
- Dependencies from `pom.xml`, including the test-scope starter.
- Endpoint `GET /customerSupportAgent` with query parameters `sessionId` and `userMessage`
  (both required) and `auth_required: false`.
- `@AiService` interface `CustomerSupportAgent` found as an agent; its `@SystemMessage` found as a
  prompt (`Prompt at line 12`).
- Tools `getBookingDetails` and `cancelBooking`, with their parameters; the agent calls both.
- Embedding store found as a vector datastore.
- No auth and no guardrail reported, as expected.

## Discrepancies

| # | Expected | Found | Evidence | Class |
|---|----------|-------|----------|-------|
| D1 | Model `gpt-4o-mini` (OpenAI) and embedding model, with `agent USES model` | No MODEL node at all, so no provider and no USES edge | Name is set in `application.properties` and an enum constant, not in a Java `model("...")` call | NuGuard detection gap |
| D2 | Only application code scanned | 5 of 9 AGENT nodes and 4 of 5 PROMPT nodes come from `src/test` (`CustomerSupportAgentIT`, `JudgeModelAssertions`, `ModelAssertion`, `TextAssertion`, `ResultAssert`) | canonical names contain `src_test`; nodes carry a `testing` block | NuGuard detection gap |
| D3 | Only the real agent calls the tools | All 9 agents `CALLS` both tools (16 wrong edges); LangChain4j `CALLS` every agent and tool | edge list | NuGuard detection gap |
| D4 | Endpoint reaches the agent | Endpoint calls `Customersupportagentcontroller` only; no edge from the controller to the `@AiService` agent, so no path from the endpoint to the prompt or tools | the controller gets the agent through its constructor | NuGuard detection gap |
| D5 | `cancelBooking` marked as a destructive or high-privilege tool | `high_privilege: false`, `privilege_scope: []` on both tools | tool node metadata | NuGuard detection gap |
| D6 | The real agent is the agentic component | `agentic: true` on `BookingTools`; `false` on `CustomerSupportAgent` and the controller | agent node metadata | NuGuard detection gap (minor) |
| D7 | In-memory booking map as a datastore, tools reaching it | Not reported | plain `HashMap`, no store marker | Possibly by design; baseline is fine |
| D8 | Chat memory and content retriever | Not reported | may have no component type | Unknown; check schema |
| D9 | Names as in source | Casing lost: `Customersupportagent`, `Cancelbooking` | node names | Cosmetic |

## Predictions written before the run (see EXPECTED_SBOM.md)
- Model missed: **correct** (D1).
- Embedding store found: **correct**.
- GET endpoint found: **correct**.
- No auth node: **correct**.
- Agent and prompt may be missed: **wrong**. Both were detected.

## Issues filed
D1: TBD, D2: TBD, D3: TBD, D4: TBD, D5: TBD, D6: TBD
