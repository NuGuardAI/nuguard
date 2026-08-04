package semgrepfixtures

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/sashabaranov/go-openai"
)

type appConfig struct {
	Model string
}

// Case 4: user/dynamic input -> fmt.Sprintf, but no LLM sink.
func OrdinarySprintfNoLLM(orderID string) {
	message := fmt.Sprintf("order %s", orderID)
	log.Println(message)
}

func StaticLLMPrompt() {
	prompt := "fixed prompt"
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: prompt},
		},
	})
}

// Case 2: trusted literal/constant -> fmt.Sprintf -> LLM.
func TrustedFormattedPromptReachesLLM() {
	model := "internal-model"
	prompt := fmt.Sprintf("Use model %s", model)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: prompt},
		},
	})
}

// Case 3: trusted application-configuration value -> fmt.Sprintf -> LLM.
func TrustedAppConfigFormattedPromptReachesLLM(cfg appConfig) {
	prompt := fmt.Sprintf("Use model %s", cfg.Model)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: prompt},
		},
	})
}

// Case 5: string parameter -> LLM directly, with no fmt.Sprintf.
func DirectStringParamToLLM(userInput string) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: userInput},
		},
	})
}
