package semgrepfixtures

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/sashabaranov/go-openai"
)

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
