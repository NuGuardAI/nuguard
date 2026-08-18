// NuGuard minimal, independently written regression fixture (Azure-flavoured).
// Inspired by Azure OpenAI / go-openai Azure config patterns — not a runnable
// app and not vendored upstream source. Uses DefaultAzureConfig +
// CreateChatCompletion shapes for today's sinks; official Azure/openai-go
// call shapes are #223. See ../ATTRIBUTION.md.
package azure_sample

import (
	"context"
	"crypto/tls"
	"net/http"

	"github.com/sashabaranov/go-openai"
)

// ChatWithInsecureTLS builds an Azure OpenAI client that disables TLS
// verification and issues a chat completion on an unbounded context.
func ChatWithInsecureTLS() {
	cfg := openai.DefaultAzureConfig(
		"SYNTHETIC_AZURE_OPENAI_KEY",
		"https://example.openai.azure.com/",
	)
	cfg.HTTPClient = &http.Client{
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{
				InsecureSkipVerify: true, //nolint:gosec // intentional insecure fixture
			},
		},
	}
	client := openai.NewClientWithConfig(cfg)
	client.CreateChatCompletion(context.Background(), openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello from azure sample"},
		},
	})
}
