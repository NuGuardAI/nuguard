// NuGuard minimal, independently written regression fixture (GCP-flavoured).
// Inspired by google.golang.org/genai / Gemini Go API patterns — not a runnable
// app and not vendored upstream source. Local two-arg GenerateContent(ctx, req)
// wrapper exercises today's sink allowlist; official multi-arg
// Models.GenerateContent is #223. See ../ATTRIBUTION.md.
package gcp_sample

import (
	"context"

	"google.golang.org/genai"
)

// geminiRequest is a minimal stand-in for a generate-content request.
type geminiRequest struct {
	Prompt string
}

// geminiCompatClient provides a rule-compatible GenerateContent sink.
type geminiCompatClient struct {
	cfg *genai.ClientConfig
}

func newGeminiCompatClient(cfg *genai.ClientConfig) *geminiCompatClient {
	return &geminiCompatClient{cfg: cfg}
}

// GenerateContent is a compatibility sink matching the current Go Semgrep allowlist.
func (c *geminiCompatClient) GenerateContent(ctx context.Context, req geminiRequest) {
	_ = c.cfg
	_ = ctx
	_ = req
}

// GenerateWithHardcodedKey uses a synthetic Gemini API key and an unbounded
// context for a generate-content call.
func GenerateWithHardcodedKey() {
	cfg := &genai.ClientConfig{}
	cfg.APIKey = "AIzaSYNTHETIC_GCP_PROVIDER_KEY"
	client := newGeminiCompatClient(cfg)
	client.GenerateContent(context.Background(), geminiRequest{
		Prompt: "hello from gcp sample",
	})
}
