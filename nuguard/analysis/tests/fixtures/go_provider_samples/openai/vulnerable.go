// NuGuard minimal, independently written regression fixture (OpenAI-flavoured).
// Inspired by go-openai / OpenAI Go API patterns — not a runnable app and not
// vendored upstream source. Uses CreateChatCompletion / DefaultConfig shapes
// that match today's sinks; official openai-go Chat.Completions.New is #223.
// See ../ATTRIBUTION.md.
package openai_sample

import (
	"context"
	"fmt"

	"github.com/sashabaranov/go-openai"
)

// ChatWithUserPrompt interpolates untrusted input into a prompt and calls the
// chat-completion API with an unbounded context and a hardcoded API key.
func ChatWithUserPrompt(userInput string) {
	cfg := openai.DefaultConfig("sk-SYNTHETIC_OPENAI_PROVIDER_KEY")
	client := openai.NewClientWithConfig(cfg)
	prompt := fmt.Sprintf("Answer the customer: %s", userInput)
	client.CreateChatCompletion(context.Background(), openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: prompt},
		},
	})
}
