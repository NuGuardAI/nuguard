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

func BoundedContextBackgroundAssignWithTimeout() {
	var ctx context.Context
	var cancel context.CancelFunc
	ctx = context.Background()
	ctx, cancel = context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func BoundedContextBackgroundAssignWithDeadline() {
	var ctx context.Context
	var cancel context.CancelFunc
	deadline := time.Now().Add(30 * time.Second)
	ctx = context.Background()
	ctx, cancel = context.WithDeadline(ctx, deadline)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func BoundedContextTODOAssignWithTimeout() {
	var ctx context.Context
	var cancel context.CancelFunc
	ctx = context.TODO()
	ctx, cancel = context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func BoundedContextTODOAssignWithDeadline() {
	var ctx context.Context
	var cancel context.CancelFunc
	deadline := time.Now().Add(30 * time.Second)
	ctx = context.TODO()
	ctx, cancel = context.WithDeadline(ctx, deadline)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func BoundedContextShortDeclBackgroundAssignTimeout() {
	ctx := context.Background()
	var cancel context.CancelFunc
	ctx, cancel = context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func BoundedContextShortDeclTODOAssignTimeout() {
	ctx := context.TODO()
	var cancel context.CancelFunc
	ctx, cancel = context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func BoundedContextShortDeclBackgroundAssignDeadline() {
	deadline := time.Now().Add(30 * time.Second)
	ctx := context.Background()
	var cancel context.CancelFunc
	ctx, cancel = context.WithDeadline(ctx, deadline)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func BoundedContextShortDeclTODOAssignDeadline() {
	deadline := time.Now().Add(30 * time.Second)
	ctx := context.TODO()
	var cancel context.CancelFunc
	ctx, cancel = context.WithDeadline(ctx, deadline)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func BoundedContextAssignBackgroundShortDeclTimeout() {
	var ctx context.Context
	ctx = context.Background()
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func BoundedContextAssignTODOShortDeclTimeout() {
	var ctx context.Context
	ctx = context.TODO()
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func BoundedContextAssignBackgroundShortDeclDeadline() {
	var ctx context.Context
	deadline := time.Now().Add(30 * time.Second)
	ctx = context.Background()
	ctx, cancel := context.WithDeadline(ctx, deadline)
	defer cancel()
	client := openai.NewClient("test-token")
	client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: openai.GPT3Dot5Turbo,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleUser, Content: "hello"},
		},
	})
}

func BoundedContextAssignTODOShortDeclDeadline() {
	var ctx context.Context
	deadline := time.Now().Add(30 * time.Second)
	ctx = context.TODO()
	ctx, cancel := context.WithDeadline(ctx, deadline)
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
