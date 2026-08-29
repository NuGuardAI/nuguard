package semgrepfixtures

import (
	"context"
	"fmt"
)

// Official SDK *streaming* call shapes (issue #232). Structural stand-ins,
// not runnable and not vendored upstream source.

type streamChatCompletions struct{}

func (streamChatCompletions) New(ctx context.Context, req any, _ ...any) {}

func (streamChatCompletions) NewStreaming(ctx context.Context, req any, _ ...any) {}

type streamChatService struct {
	Completions streamChatCompletions
}

type streamResponsesService struct{}

func (streamResponsesService) NewStreaming(ctx context.Context, req any, _ ...any) {}

type streamMessagesService struct{}

func (streamMessagesService) New(ctx context.Context, req any, _ ...any) {}

func (streamMessagesService) NewStreaming(ctx context.Context, req any, _ ...any) {}

type streamModelsService struct{}

func (streamModelsService) GenerateContent(ctx context.Context, model string, contents any, config any, _ ...any) {
}

func (streamModelsService) GenerateContentStream(ctx context.Context, model string, contents any, config any, _ ...any) {
}

type streamOpenAIClient struct {
	Chat      streamChatService
	Responses streamResponsesService
}

type streamAnthropicClient struct {
	Messages streamMessagesService
}

type streamBedrockClient struct{}

func (streamBedrockClient) ConverseStream(ctx context.Context, req any, _ ...any) {}

func (streamBedrockClient) InvokeModelWithResponseStream(ctx context.Context, req any, _ ...any) {}

type streamGeminiClient struct {
	Models streamModelsService
}

// StreamingPromptInjectionOpenAI: user input -> fmt.Sprintf -> official
// streaming sink = prompt-injection MATCH (#232).
func StreamingPromptInjectionOpenAI(userInput string) {
	client := streamOpenAIClient{}
	prompt := fmt.Sprintf("Answer: %s", userInput)
	stream := client.Chat.Completions.NewStreaming(context.Background(), prompt)
	_ = stream
}

// StreamingMissingTimeoutAnthropic: context.Background() straight into an
// official streaming sink = missing-timeout MATCH (#232).
func StreamingMissingTimeoutAnthropic() {
	client := streamAnthropicClient{}
	client.Messages.NewStreaming(context.Background(), "hello")
}

// StreamingMissingTimeoutBedrockConverseStream = missing-timeout MATCH.
func StreamingMissingTimeoutBedrockConverseStream() {
	client := streamBedrockClient{}
	client.ConverseStream(context.Background(), "hello")
}

// StreamingMissingTimeoutBedrockInvokeModelWithResponseStream = MATCH.
func StreamingMissingTimeoutBedrockInvokeModelWithResponseStream() {
	client := streamBedrockClient{}
	client.InvokeModelWithResponseStream(context.Background(), "hello")
}

// StreamingPromptInjectionGemini: user input -> fmt.Sprintf ->
// Models.GenerateContentStream = prompt-injection MATCH (#232).
func StreamingPromptInjectionGemini(userInput string) {
	client := streamGeminiClient{}
	prompt := fmt.Sprintf("Summarize: %s", userInput)
	it := client.Models.GenerateContentStream(context.Background(), "gemini-2.0-flash", prompt, nil)
	_ = it
}

// StreamingBoundedContextIsNotReported: a timeout-bounded streaming call with
// trusted input must not trip either rule.
func StreamingBoundedContextIsNotReported(userInput string) {
	client := streamOpenAIClient{}
	ctx, cancel := context.WithTimeout(context.Background(), 30)
	defer cancel()
	resp := client.Responses.NewStreaming(ctx, userInput)
	_ = resp
}
