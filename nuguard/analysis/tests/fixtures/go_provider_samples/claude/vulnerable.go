// NuGuard minimal, independently written regression fixture (Claude-flavoured).
// Inspired by anthropic-sdk-go API patterns — not a runnable app and not
// vendored upstream source. Local CreateMessage(ctx, req) wrapper exercises
// today's sink allowlist; official Messages.New is #223.
// See ../ATTRIBUTION.md.
package claude_sample

import (
	"context"

	"github.com/anthropics/anthropic-sdk-go"
)

// claudeMessageRequest is a minimal stand-in for a Messages API request.
type claudeMessageRequest struct {
	Prompt string
}

// claudeCompatClient provides a rule-compatible CreateMessage sink.
type claudeCompatClient struct {
	apiKey string
}

func newClaudeCompatClient(apiKey string) *claudeCompatClient {
	return &claudeCompatClient{apiKey: apiKey}
}

// CreateMessage is a compatibility sink matching the current Go Semgrep allowlist.
func (c *claudeCompatClient) CreateMessage(ctx context.Context, req claudeMessageRequest) {
	_ = c.apiKey
	_ = ctx
	_ = req
	_ = anthropic.ModelClaudeOpus4_6
}

// ChatWithHardcodedToken uses a synthetic Anthropic auth token and an
// unbounded context for a create-message call.
func ChatWithHardcodedToken() {
	cfg := struct{ AuthToken string }{}
	cfg.AuthToken = "sk-SYNTHETIC_CLAUDE_PROVIDER_KEY"
	client := newClaudeCompatClient(cfg.AuthToken)
	client.CreateMessage(context.Background(), claudeMessageRequest{
		Prompt: "hello from claude sample",
	})
}
