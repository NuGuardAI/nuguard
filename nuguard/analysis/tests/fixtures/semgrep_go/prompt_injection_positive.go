package semgrepfixtures

import (
	"context"
	"fmt"

	"github.com/sashabaranov/go-openai"
)

func PromptInjectionAssignmentThenCall(userInput string) {
	prompt := fmt.Sprintf("Answer this user: %s", userInput)
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(context.Background(), openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: prompt},
		},
	})
}

func PromptInjectionInlineCall(userInput string) {
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(context.Background(), openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: fmt.Sprintf("Summarize: %s", userInput)},
		},
	})
}
