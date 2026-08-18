package semgrepfixtures

import (
	"context"
	"crypto/tls"
	"net/http"

	"github.com/sashabaranov/go-openai"
)

type internalConfig struct {
	HTTPClient *http.Client
}

func UnrelatedClientsSameFunction(ctx context.Context, llmClient *openai.Client, request openai.ChatCompletionRequest) {
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{
			InsecureSkipVerify: true,
		},
	}
	unrelatedHTTP := &http.Client{Transport: transport}
	internalConfig := internalConfig{}
	internalConfig.HTTPClient = unrelatedHTTP

	llmClient.CreateChatCompletion(ctx, request)
}

func SecureTLSExplicitFalse() {
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{
			InsecureSkipVerify: false,
		},
	}
	_ = &http.Client{Transport: transport}
}

func DefaultSecureTransport() {
	_ = &http.Client{}
}

func UnrelatedTLSNoLLMContext() {
	cfg := &tls.Config{
		InsecureSkipVerify: true,
	}
	_ = cfg
}

type genericHTTPConfig struct {
	HTTPClient *http.Client
}

func UnrelatedHTTPClientField(cfg genericHTTPConfig) {
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{
			InsecureSkipVerify: true,
		},
	}
	httpClient := &http.Client{Transport: transport}
	cfg.HTTPClient = httpClient
	_, _ = cfg.HTTPClient.Get("https://example.com")
}
