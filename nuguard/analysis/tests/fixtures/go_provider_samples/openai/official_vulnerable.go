// NuGuard minimal official openai-go call-shape fixture (issue #223).
// Independently written structural sample — not vendored upstream source.
// See ../ATTRIBUTION.md.
package openai_sample

import (
	"context"
	"fmt"
)

type openaiChatCompletions struct{}

func (openaiChatCompletions) New(ctx context.Context, req any, _ ...any) {}

type openaiChatService struct {
	Completions openaiChatCompletions
}

type openaiResponsesService struct{}

func (openaiResponsesService) New(ctx context.Context, req any, _ ...any) {}

type openaiOfficialClient struct {
	Chat      openaiChatService
	Responses openaiResponsesService
}

// OfficialChatCompletionsWithUserPrompt exercises Chat.Completions.New.
func OfficialChatCompletionsWithUserPrompt(userInput string) {
	client := openaiOfficialClient{}
	prompt := fmt.Sprintf("Answer the customer: %s", userInput)
	client.Chat.Completions.New(context.Background(), prompt)
}

// OfficialResponsesMissingTimeout exercises Responses.New on an unbounded context.
func OfficialResponsesMissingTimeout() {
	client := openaiOfficialClient{}
	client.Responses.New(context.Background(), "hello from openai official sample")
}
