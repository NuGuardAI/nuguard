package semgrepfixtures

import (
	"context"
	"database/sql"
	"time"

	"github.com/sashabaranov/go-openai"
)

func BoundedContextWithTimeout() {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func BoundedContextWithDeadline() {
	deadline := time.Now().Add(30 * time.Second)
	ctx, cancel := context.WithDeadline(context.Background(), deadline)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func NonLLMBackgroundContext(db *sql.DB) {
	ctx := context.Background()
	_, _ = db.QueryContext(ctx, "SELECT 1")
}

func LLMWithParameterContext(ctx context.Context) {
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}
