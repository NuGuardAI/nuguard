package main

// chat.go mirrors the shape found in mosaic-care/healthcare-service's
// backend/chat/chat.go: a hand-rolled HTTP client calling the Anthropic
// API directly, with no anthropic-sdk-go import for AnthropicSDKGoAdapter
// to key off — the exact gap phase 8's direct-HTTP LLM detection covers.

import (
	"net/http"
)

const anthropicURL = "https://api.anthropic.com/v1/messages"
const chatModel = "claude-sonnet-4-6"

type anthropicReq struct {
	Model string `json:"model"`
}

func callAnthropic(apiKey string, body anthropicReq) {
	httpReq, _ := http.NewRequest(http.MethodPost, anthropicURL, nil)
	httpReq.Header.Set("anthropic-version", "2023-06-01")
	_ = chatModel
}
