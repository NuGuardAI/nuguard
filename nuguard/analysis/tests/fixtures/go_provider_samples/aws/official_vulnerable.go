// NuGuard minimal official Bedrock Runtime call-shape fixture (issue #223).
// Independently written structural sample — not vendored upstream source.
// Includes a trailing optFn argument to exercise variadic sink matching.
// See ../ATTRIBUTION.md.
package aws_sample

import (
	"context"
	"fmt"
)

type bedrockOfficialClient struct{}

func (bedrockOfficialClient) Converse(ctx context.Context, req any, _ ...any) {}

func (bedrockOfficialClient) InvokeModel(ctx context.Context, req any, _ ...any) {}

// OfficialConverseWithUserPromptAndOptFn exercises Client.Converse with optFns.
func OfficialConverseWithUserPromptAndOptFn(userInput string) {
	client := bedrockOfficialClient{}
	prompt := fmt.Sprintf("Summarize ticket: %s", userInput)
	optFn := func() {}
	client.Converse(context.Background(), prompt, optFn)
}

// OfficialInvokeModelMissingTimeoutWithOptFn exercises Client.InvokeModel with optFns.
func OfficialInvokeModelMissingTimeoutWithOptFn() {
	client := bedrockOfficialClient{}
	optFn := func() {}
	client.InvokeModel(context.Background(), "hello from aws official sample", optFn)
}
