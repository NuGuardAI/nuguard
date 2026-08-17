// NuGuard minimal Azure OpenAI-compatible official call-shape fixture (#223).
// Independently written structural sample — not vendored upstream source.
// Exercises Chat.Completions.New / Responses.New only. Official
// option.WithHTTPClient TLS coverage is intentionally out of scope.
// See ../ATTRIBUTION.md.
package azure_sample

import (
	"context"
	"fmt"
)

type azureChatCompletions struct{}

func (azureChatCompletions) New(ctx context.Context, req any, _ ...any) {}

type azureChatService struct {
	Completions azureChatCompletions
}

type azureResponsesService struct{}

func (azureResponsesService) New(ctx context.Context, req any, _ ...any) {}

type azureOfficialClient struct {
	Chat      azureChatService
	Responses azureResponsesService
}

// OfficialChatCompletionsWithUserPrompt exercises Chat.Completions.New.
func OfficialChatCompletionsWithUserPrompt(userInput string) {
	client := azureOfficialClient{}
	prompt := fmt.Sprintf("Answer the customer: %s", userInput)
	client.Chat.Completions.New(context.Background(), prompt)
}

// OfficialResponsesMissingTimeout exercises Responses.New on an unbounded context.
func OfficialResponsesMissingTimeout() {
	client := azureOfficialClient{}
	client.Responses.New(context.Background(), "hello from azure official sample")
}
