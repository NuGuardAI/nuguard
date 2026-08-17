// NuGuard minimal, independently written regression fixture (AWS-flavoured).
// Inspired by AWS Bedrock Runtime Go v2 API patterns — not a runnable app and
// not vendored upstream source. Bedrock Runtime import is for flavour only;
// local SendMessage(ctx, req) wrapper exercises today's sink allowlist.
// Converse/InvokeModel support is #223. See ../ATTRIBUTION.md.
package aws_sample

import (
	"context"
	"fmt"

	"github.com/aws/aws-sdk-go-v2/service/bedrockruntime"
)

// bedrockChatRequest is a minimal stand-in for a chat request body.
type bedrockChatRequest struct {
	Prompt string
}

// bedrockCompatClient wraps Bedrock Runtime with a rule-compatible sink method.
// Official Client.Converse / InvokeModel matching is issue #223.
type bedrockCompatClient struct {
	runtime *bedrockruntime.Client
}

func newBedrockCompatClient(runtime *bedrockruntime.Client) *bedrockCompatClient {
	return &bedrockCompatClient{runtime: runtime}
}

// SendMessage is a compatibility sink matching the current Go Semgrep allowlist.
func (c *bedrockCompatClient) SendMessage(ctx context.Context, req bedrockChatRequest) {
	_ = c.runtime
	_ = ctx
	_ = req
}

// AskWithUserPrompt formats untrusted input and sends it via the compatibility
// sink using an unbounded root context.
func AskWithUserPrompt(userInput string) {
	// runtime is nil in this offline fixture; Semgrep only needs the call graph.
	client := newBedrockCompatClient(nil)
	prompt := fmt.Sprintf("Summarize ticket: %s", userInput)
	client.SendMessage(context.Background(), bedrockChatRequest{Prompt: prompt})
}
