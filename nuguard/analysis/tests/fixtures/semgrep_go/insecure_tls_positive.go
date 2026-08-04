package semgrepfixtures

import (
	"context"
	"crypto/tls"
	"net/http"

	"github.com/sashabaranov/go-openai"
)

func InsecureTLSWithLLMClient() {
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{
			InsecureSkipVerify: true,
		},
	}
	httpClient := &http.Client{Transport: transport}
	cfg := openai.DefaultConfig("test-token")
	cfg.HTTPClient = httpClient
	client := openai.NewClientWithConfig(*cfg)
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
	client := openai.NewClientWithConfig(*cfg)
	client.CreateChatCompletion(context.Background(), openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}
