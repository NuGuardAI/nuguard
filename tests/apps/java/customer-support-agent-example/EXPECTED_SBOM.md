# Expected AI-SBOM: customer-support-agent-example

Written by reading the source at upstream commit `9d441958edc7f5a91a716271b4f8748af2c854ec`
(langchain4j/langchain4j-examples, Apache-2.0) **before running any NuGuard command against it**
(issue #615). This file is committed first so the git history shows the order. Do not edit it after
the run. Differences found later go in `DISCREPANCIES.md`.

## What the source is

A car-rental support chatbot ("Roger", Miles of Smiles). Spring Boot 3.4.2, Java 17, LangChain4j
1.21.0-beta31 with the OpenAI starter. One HTTP endpoint hands the user's message to an
`@AiService` interface; the model can call two `@Tool` methods that work on a static in-memory
booking map. The terms-of-use text is loaded into an in-memory embedding store for retrieval.
No Spring Security, no login, no guardrail classes.

## Expected components

| Type | Expected | Evidence |
|------|----------|----------|
| Framework | Spring Boot 3.4.2 / Spring MVC | `pom.xml` parent, `spring-boot-starter-web`, `@RestController` |
| Framework | LangChain4j | `dev.langchain4j.*` imports, `langchain4j-spring-boot-starter` |
| Dependencies | `langchain4j-spring-boot-starter`, `langchain4j-open-ai-spring-boot-starter`, `langchain4j-embeddings-all-minilm-l6-v2`, `spring-boot-starter-web` (all declared in `pom.xml`) | `pom.xml` |
| API endpoint | `GET /customerSupportAgent`, query parameters `sessionId` and `userMessage` (both required), returns plain text | `CustomerSupportAgentController` |
| Agent | `CustomerSupportAgent`, an `@AiService` interface with `answer(@MemoryId String, @UserMessage String)` returning `Result<String>` | `CustomerSupportAgent.java` |
| Prompt | System prompt on `answer()`: persona Roger, three rules (collect name, surname and booking number first; confirm before cancelling; business topics only), `{{current_date}}` | `@SystemMessage` text block |
| Model | OpenAI `gpt-4o-mini`, temperature 0.0, strict tools, 60 s timeout; API key from env `OPENAI_API_KEY`. Built by the starter, not in code | `application.properties` |
| Model (secondary) | `AllMiniLmL6V2EmbeddingModel`, local in-process ONNX, no network | `CustomerSupportAgentConfiguration.embeddingModel` |
| Not a model call | `OpenAiTokenCountEstimator(GPT_4_O_MINI)` only counts tokens. It should not be reported as a second chat model | `CustomerSupportAgentConfiguration` |
| Tool | `getBookingDetails(bookingNumber, customerName, customerSurname)` returns `Booking`. Read-only; returns customer name, surname and booking dates | `BookingTools.java` |
| Tool | `cancelBooking(bookingNumber, customerName, customerSurname)` returns void. Destructive write: removes the booking | `BookingTools.java`, `BookingService.java` |
| Datastore | `InMemoryEmbeddingStore<TextSegment>` (vector) holding `miles-of-smiles-terms-of-use.txt` in 100-token segments, ingested at startup | `CustomerSupportAgentConfiguration` |
| Datastore | Static in-memory `HashMap` of bookings, seeded with `MS-777`, customer John Doe, 2025-12-13 to 2025-12-31 | `BookingService.java` |
| Retrieval | `EmbeddingStoreContentRetriever`, maxResults 1, minScore 0.6 | `CustomerSupportAgentConfiguration` |
| Memory | `TokenWindowChatMemory`, 5000 tokens, one per `memoryId` (the caller-supplied `sessionId`) | `chatMemoryProvider` bean |
| Auth | **None** | no Spring Security dependency, no auth annotations |
| Guardrail | **None in code**. Rules exist only as prompt text | no guardrail classes |

## Expected relationships

- Endpoint `GET /customerSupportAgent` calls agent `CustomerSupportAgent`.
- Agent uses model `gpt-4o-mini`.
- Agent calls tools `getBookingDetails` and `cancelBooking`.
- Agent reads from the embedding store through the content retriever.
- Both tools reach the in-memory booking map; `cancelBooking` writes to it.
- Agent uses chat memory keyed by `sessionId`.

## Expected analyze observations (human judgement, not NuGuard output)

- An unauthenticated endpoint reaches a tool that deletes data.
- No guardrail sits between user text and the tools.
- The "confirm before cancelling" rule exists only in the prompt. Code does not enforce it.
- Identity is a claim made in chat (name, surname, booking number), not an authenticated session.

## Hypotheses for behavior and red-team (to be triaged as real or false positive)

1. Anyone who knows `MS-777 / John / Doe` can read or cancel the booking (knowledge-based identity).
2. `sessionId` is chosen by the caller and `test.http` uses `1`, so another conversation's memory
   can be continued by guessing the id.
3. Prompt injection can skip the confirmation step before `cancelBooking`.
4. The off-topic rule holds only as far as the model follows the prompt.
5. The system prompt can be extracted.

## Harness compatibility questions (from the NuGuard docs, before testing)

- The docs describe per-request HTTP POST with a JSON body. This app is `GET` with query parameters.
- The app returns plain text, not `{"response": "..."}`.
- `sessionId` is required and keys the memory; the harness needs a stable id per conversation.

## Predictions about detection, written after reading `java_ai.py` / `java_web.py` and before running them

These are guesses to check, not expectations about the app.

- Model likely missed: the adapter reads model names from Java `model("...")` or `MODEL = "..."`
  shapes. Here the name is in `application.properties` and in an enum constant.
- Agent and prompt may be missed: the adapter's markers include `AiServices` and `ChatModel` but not
  the `@AiService` annotation, and the prompt is an annotation text block.
- Tools are detected by `@Tool`, but linking them to an agent may fail if no agent is found.
- The embedding store should be found (`EmbeddingStore` marker).
- The GET endpoint with `@RequestParam` should be found.
- No auth node should appear.
