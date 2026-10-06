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
D1: #642, D2: #640, D3: #641, D4: #643, D5: #644, D6: #641 (with D3), D10: #645, D11: #646.
The chat client's POST-only limitation (not an SBOM discrepancy, found while wiring the target): #647.
Not filed: D7 (by design, a plain HashMap has no store marker), D8 (unclear whether a memory or retriever type exists), D9 (cosmetic name casing, listed in the gap list).

## Additional discrepancies (from `analyze` and Semgrep)

| # | Expected | Found | Evidence | Class |
|---|----------|-------|----------|-------|
| D10 | Dedicated Java Semgrep rules flag user input reaching the model and an LLM answer returned from a controller | 0 Semgrep findings, although `userMessage` (a `@RequestParam`) goes straight to the `@AiService` method and its answer is returned unchecked | `java-ai-prompt-injection` only treats servlet-style calls (`getParameter`, `getHeader`, ...) as sources and only `.prompt(x)`, `.call(x)`, `.generate(x)`, `.chat(x)`, `.complete(x)` as sinks; `java-ai-unvalidated-controller-response` needs `return $SERVICE.chat(...)`. This app calls `agent.answer(sessionId, userMessage)` | NuGuard detection gap |
| D11 | No critical CVE for a Spring Boot 3.4.2 app | CRITICAL GHSA-36P3-WJMG-H94X (Spring4Shell, Spring Framework 5.x) on `spring-boot-starter-web`, with remediation text about `5.2.20.RELEASE` and an OS package in a container image | The SBOM dependency has no version (`pkg:maven/org.springframework.boot/spring-boot-starter-web`, no `@version`) because the version comes from `spring-boot-starter-parent` | NuGuard detection gap (false positive) |

## `analyze` triage (25 findings, exit code 1 = findings reported)

| Finding | Verdict | Note |
|---------|---------|------|
| CRITICAL OSV GHSA-36P3-WJMG-H94X | False positive | D11 |
| NGA-006 missing authentication on `GET /customerSupportAgent` | True positive | no authentication exists |
| NGA-002 no output guardrail on an internet-capable agent | True positive | reported on the controller node, not the real agent (D4) |
| NGA-009 no audit logging | True positive | the app has none; component list inflated by D2 |
| NGA-012 invoke tool without HITL approval (18 findings) | 1 true positive, 17 noise or debatable | `CustomerSupportAgent` to `cancelBooking` is real. The rest come from test classes, the config class, the controller and the tool holder (D2, D3). `getBookingDetails` is read-only (D5) |
| NGA-026 no rate limiting, NGA-027 no security headers | True positives | the app has neither |
| ATLAS-NC-002 writable datastore reachable by unguarded agent (embedding store) | Likely false positive, to verify | the store is filled at startup and only read afterwards |
