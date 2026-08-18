package semgrepfixtures

import (
	"context"

	"github.com/sashabaranov/go-openai"
)

func MissingTimeoutAssignmentThenCall() {
	ctx := context.Background()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func MissingTimeoutInlineBackground() {
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(context.Background(), openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}
