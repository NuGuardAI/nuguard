package semgrepfixtures

import (
	"context"
	"crypto/tls"
	"net/http"

	"github.com/sashabaranov/go-openai"
)

// providerHTTPConfig is a minimal local config type used to exercise the
// pointer-dereference constructor form (*cfg) in a compile-valid way without
// pretending go-openai's ClientConfig is itself a pointer API.
type providerHTTPConfig struct {
	HTTPClient *http.Client
}

type providerLLMClient struct{}

func newProviderLLMClient(cfg providerHTTPConfig) *providerLLMClient {
	return &providerLLMClient{}
}

func (c *providerLLMClient) CreateChatCompletion(ctx context.Context, req openai.ChatCompletionRequest) {
}

func InsecureTLSWithLLMClient() {
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{
			InsecureSkipVerify: true,
		},
	}
	httpClient := &http.Client{Transport: transport}
	cfg := openai.DefaultConfig("test-token")
	cfg.HTTPClient = httpClient
	client := openai.NewClientWithConfig(cfg)
	client.CreateChatCompletion(context.Background(), openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func InsecureTLSNestedClientLiteral() {
	cfg := openai.DefaultConfig("test-token")
	cfg.HTTPClient = &http.Client{
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{
				InsecureSkipVerify: true,
			},
		},
	}
	client := openai.NewClientWithConfig(cfg)
	client.CreateChatCompletion(context.Background(), openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func InsecureTLSPointerDerefConfig() {
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{
			InsecureSkipVerify: true,
		},
	}
	httpClient := &http.Client{Transport: transport}
	cfg := &providerHTTPConfig{}
	cfg.HTTPClient = httpClient
	client := newProviderLLMClient(*cfg)
	client.CreateChatCompletion(context.Background(), openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}
