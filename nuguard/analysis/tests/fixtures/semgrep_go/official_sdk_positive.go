package semgrepfixtures

import (
	"context"
	"fmt"
)

// Minimal structural stand-ins for official SDK call shapes (issue #223).
// Not runnable and not vendored upstream source.

type officialChatCompletions struct{}

func (officialChatCompletions) New(ctx context.Context, req any, _ ...any) {}

type officialChatService struct {
	Completions officialChatCompletions
}

type officialResponsesService struct{}

func (officialResponsesService) New(ctx context.Context, req any, _ ...any) {}

type officialMessagesService struct{}

func (officialMessagesService) New(ctx context.Context, req any, _ ...any) {}

type officialModelsService struct{}

func (officialModelsService) GenerateContent(ctx context.Context, model string, contents any, config any, _ ...any) {
}

type officialOpenAIClient struct {
	Chat      officialChatService
	Responses officialResponsesService
}

type officialAnthropicClient struct {
	Messages officialMessagesService
}

type officialBedrockClient struct{}

func (officialBedrockClient) Converse(ctx context.Context, req any, _ ...any) {}

func (officialBedrockClient) InvokeModel(ctx context.Context, req any, _ ...any) {}

type officialGeminiClient struct {
	Models officialModelsService
}

func OfficialOpenAIChatCompletionsPromptInjection(userInput string) {
	client := officialOpenAIClient{}
	prompt := fmt.Sprintf("Answer: %s", userInput)
	client.Chat.Completions.New(context.Background(), prompt)
}

func OfficialOpenAIResponsesMissingTimeout() {
	client := officialOpenAIClient{}
	client.Responses.New(context.Background(), "hello")
}

func OfficialAnthropicMessagesPromptInjection(userInput string) {
	client := officialAnthropicClient{}
	prompt := fmt.Sprintf("Discuss: %s", userInput)
	client.Messages.New(context.Background(), prompt)
}

func OfficialBedrockConverseMissingTimeout() {
	client := officialBedrockClient{}
	client.Converse(context.Background(), "hello")
}

// OfficialBedrockInvokeModelWithOptFn proves variadic optFns matching (#223).
func OfficialBedrockInvokeModelWithOptFn() {
	client := officialBedrockClient{}
	optFn := func() {}
	client.InvokeModel(context.Background(), "hello", optFn)
}

func OfficialBedrockConversePromptInjectionWithOptFn(userInput string) {
	client := officialBedrockClient{}
	prompt := fmt.Sprintf("Summarize ticket: %s", userInput)
	optFn := func() {}
	client.Converse(context.Background(), prompt, optFn)
}

func OfficialGeminiGenerateContentPromptInjection(userInput string) {
	client := officialGeminiClient{}
	prompt := fmt.Sprintf("Summarize: %s", userInput)
	client.Models.GenerateContent(context.Background(), "gemini-2.0-flash", prompt, nil)
}
