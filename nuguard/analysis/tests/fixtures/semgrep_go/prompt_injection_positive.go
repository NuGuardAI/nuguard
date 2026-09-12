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

type message struct {
	Role    string
	Content string
}

type anthropicReq struct {
	Messages []message
}

func callAnthropic(apiKey string, body anthropicReq) {}

// Case 6: apps that talk to an LLM over raw net/http instead of an official
// SDK client have no $CLIENT.$METHOD(...) shape for the rule to match — the
// wrapper call itself (matched by name) is the only usable sink.
func PromptInjectionRawHTTPWrapper(apiKey, userInput string) {
	prompt := fmt.Sprintf("A user asked: %q", userInput)
	body := anthropicReq{Messages: []message{{Role: "user", Content: prompt}}}
	callAnthropic(apiKey, body)
}
