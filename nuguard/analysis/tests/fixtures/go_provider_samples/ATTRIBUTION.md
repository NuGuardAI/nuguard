# Go provider Semgrep sample attribution

Minimal, independently written regression fixtures for NuGuard's bundled Go
AI-security Semgrep rules. They are inspired by / based on the API and
configuration patterns documented in the linked provider sources. They are
**not** full applications.

No upstream application or source file is vendored wholesale. Linked upstream
projects and docs retain their own applicable licenses.

Each provider directory includes:

- `vulnerable.go` — community / provider-neutral compatibility shapes that
  exercise today's baseline sink allowlist (`CreateChatCompletion`,
  `CreateMessage`, local two-arg `GenerateContent`, `SendMessage`, …), from
  PR #224. Azure TLS coverage remains on this community `CFG.HTTPClient` path.
- `official_vulnerable.go` — independently written structural samples of the
  official non-streaming SDK call shapes added in issue **#223**
  (`Chat.Completions.New`, `Responses.New`, Bedrock `Converse` /
  `InvokeModel` including optional trailing optFns, Anthropic `Messages.New`,
  multi-arg `Models.GenerateContent`). Azure is covered via OpenAI-compatible
  nested New call shapes only (not official TLS).

Streaming APIs, credential-option patterns (`option.WithAPIKey`, …), and
official `option.WithHTTPClient` TLS flows remain out of scope.

| Provider | Patterns documented in | Fixture notes |
|----------|------------------------|---------------|
| OpenAI | [openai-go](https://github.com/openai/openai-go); community [go-openai](https://github.com/sashabaranov/go-openai) | `vulnerable.go`: `CreateChatCompletion` / `DefaultConfig`. `official_vulnerable.go`: `Chat.Completions.New` / `Responses.New`. |
| Azure | Azure OpenAI + [go-openai Azure config](https://github.com/sashabaranov/go-openai#azure-openai-chatgpt) docs | `vulnerable.go`: `DefaultAzureConfig` + insecure TLS + `CreateChatCompletion`. `official_vulnerable.go`: OpenAI-compatible `Chat.Completions.New` / `Responses.New` (no official TLS). |
| AWS | [AWS SDK for Go v2 Bedrock Runtime examples](https://docs.aws.amazon.com/sdk-for-go/v2/developer-guide/go_bedrock-runtime_code_examples.html) | `vulnerable.go`: local `SendMessage` wrapper. `official_vulnerable.go`: `Converse` / `InvokeModel` with trailing optFns. |
| GCP | [google.golang.org/genai](https://pkg.go.dev/google.golang.org/genai) / Gemini Go docs | `vulnerable.go`: two-arg local `GenerateContent`. `official_vulnerable.go`: multi-arg `Models.GenerateContent`. |
| Claude | [anthropic-sdk-go](https://github.com/anthropics/anthropic-sdk-go) docs | `vulnerable.go`: local `CreateMessage`. `official_vulnerable.go`: `Messages.New`. |

LangGraph is intentionally omitted from this pack (no official LangGraph Go SDK;
handled separately from this follow-up).

All credentials in these fixtures are synthetic placeholders (`sk-…`, `AIza…`)
and must never be treated as real secrets.
