# Go provider Semgrep sample attribution

Minimal, independently written regression fixtures for NuGuard's bundled Go
AI-security Semgrep rules. They are inspired by / based on the API and
configuration patterns documented in the linked provider sources. They are
**not** full applications.

No upstream application or source file is vendored wholesale. Linked upstream
projects and docs retain their own applicable licenses.

Official first-party SDK call shapes that do not match today's sink allowlist
(for example `Chat.Completions.New`, Bedrock `Converse`, Anthropic
`Messages.New`, or multi-arg `Models.GenerateContent`) are intentionally **not**
exercised here. That work is tracked separately in issue **#223**
("Extend Go AI security rules for official provider SDK patterns").

Thin local compatibility wrappers exist only to exercise today's NuGuard sink
allowlist (`CreateChatCompletion`, `CreateMessage`, `GenerateContent`,
`SendMessage`, …). Official SDK call-shape support remains #223.

| Provider | Patterns documented in | Fixture notes |
|----------|------------------------|---------------|
| OpenAI | [openai-go](https://github.com/openai/openai-go); community [go-openai](https://github.com/sashabaranov/go-openai) | Independently written sample using `CreateChatCompletion` / `DefaultConfig` shapes compatible with current rules. Official `Chat.Completions.New` coverage is #223. |
| Azure | Azure OpenAI + [go-openai Azure config](https://github.com/sashabaranov/go-openai#azure-openai-chatgpt) docs | Independently written sample using Azure-style `DefaultAzureConfig` / `NewClientWithConfig` with an insecure TLS HTTP client. Official Azure/`openai-go` sinks are #223. |
| AWS | [AWS SDK for Go v2 Bedrock Runtime examples](https://docs.aws.amazon.com/sdk-for-go/v2/developer-guide/go_bedrock-runtime_code_examples.html) | Independently written sample; Bedrock Runtime import for flavour; LLM sink is a local `SendMessage(ctx, req)` wrapper because `Converse` / `InvokeModel` are #223. |
| GCP | [google.golang.org/genai](https://pkg.go.dev/google.golang.org/genai) / Gemini Go docs | Independently written sample using `APIKey` + a two-arg local `GenerateContent(ctx, req)` wrapper; official multi-arg `Models.GenerateContent` is #223. |
| Claude | [anthropic-sdk-go](https://github.com/anthropics/anthropic-sdk-go) docs | Independently written sample using `AuthToken` + a local `CreateMessage(ctx, req)` wrapper; official `Messages.New` is #223. |

LangGraph is intentionally omitted from this pack (no official LangGraph Go SDK;
handled separately from this follow-up).

All credentials in these fixtures are synthetic placeholders (`sk-…`, `AIza…`)
and must never be treated as real secrets.
