package ai

import (
	"context"
	"fmt"
	"strings"

	openai "github.com/openai/openai-go/v3"
	"github.com/openai/openai-go/v3/shared"

	"agent-desk/internal/models"
)

type ToolDefinition struct {
	Name        string
	Description string
	Parameters  map[string]any
}

type ToolCall struct {
	ID        string
	Name      string
	Arguments string
}

type ToolCallExecutor func(context.Context, ToolCall) (string, error)

type ToolLoopResult struct {
	ChatCompletionResult
	ToolCalls []ToolCall
}

// ChatWithTools executes a bounded OpenAI-compatible function-calling loop.
// Tool execution stays in the caller so business operations remain behind the
// application Tool Registry and Service layer.
func (s *llm) ChatWithTools(ctx context.Context, config models.AIConfig, systemPrompt, userPrompt string, definitions []ToolDefinition, maxSteps int, execute ToolCallExecutor) (*ToolLoopResult, error) {
	if len(definitions) == 0 || execute == nil {
		result, err := s.ChatWithConfig(ctx, config, systemPrompt, userPrompt)
		if err != nil {
			return nil, err
		}
		return &ToolLoopResult{ChatCompletionResult: *result}, nil
	}
	if maxSteps <= 0 {
		maxSteps = 5
	}
	messages := []openai.ChatCompletionMessageParamUnion{}
	if strings.TrimSpace(systemPrompt) != "" {
		messages = append(messages, openai.ChatCompletionMessageParamUnion{OfSystem: &openai.ChatCompletionSystemMessageParam{Content: openai.ChatCompletionSystemMessageParamContentUnion{OfString: openai.String(systemPrompt)}}})
	}
	messages = append(messages, openai.ChatCompletionMessageParamUnion{OfUser: &openai.ChatCompletionUserMessageParam{Content: openai.ChatCompletionUserMessageParamContentUnion{OfString: openai.String(userPrompt)}}})
	tools := make([]openai.ChatCompletionToolUnionParam, 0, len(definitions))
	for _, definition := range definitions {
		tools = append(tools, openai.ChatCompletionToolUnionParam{OfFunction: &openai.ChatCompletionFunctionToolParam{Function: shared.FunctionDefinitionParam{Name: definition.Name, Description: openai.String(definition.Description), Parameters: shared.FunctionParameters(definition.Parameters)}}})
	}
	client := newOpenAIClient(config)
	allCalls := make([]ToolCall, 0)
	for step := 0; step < maxSteps; step++ {
		params := openai.ChatCompletionNewParams{Messages: messages, Model: shared.ChatModel(config.ModelName), Tools: tools}
		if config.MaxOutputTokens > 0 {
			params.MaxCompletionTokens = openai.Int(int64(config.MaxOutputTokens))
		}
		applyProviderSpecificChatParams(&params, config)
		response, err := client.Chat.Completions.New(ctx, params)
		if err != nil {
			return nil, fmt.Errorf("tool loop chat completion failed: %w", err)
		}
		if len(response.Choices) == 0 {
			return nil, fmt.Errorf("tool loop returned no choices")
		}
		message := response.Choices[0].Message
		if len(message.ToolCalls) == 0 {
			return &ToolLoopResult{ChatCompletionResult: ChatCompletionResult{Content: strings.TrimSpace(message.Content), ModelName: config.ModelName, PromptTokens: int(response.Usage.PromptTokens), CompletionTokens: int(response.Usage.CompletionTokens)}, ToolCalls: allCalls}, nil
		}
		messages = append(messages, message.ToParam())
		for _, rawCall := range message.ToolCalls {
			call := rawCall.AsFunction()
			if call.ID == "" || call.Function.Name == "" {
				return nil, fmt.Errorf("tool loop received unsupported tool call")
			}
			toolCall := ToolCall{ID: call.ID, Name: call.Function.Name, Arguments: call.Function.Arguments}
			allCalls = append(allCalls, toolCall)
			output, callErr := execute(ctx, toolCall)
			if callErr != nil {
				output = "tool execution failed: " + callErr.Error()
			}
			messages = append(messages, openai.ChatCompletionMessageParamUnion{OfTool: &openai.ChatCompletionToolMessageParam{ToolCallID: call.ID, Content: openai.ChatCompletionToolMessageParamContentUnion{OfString: openai.String(output)}}})
		}
	}
	return nil, fmt.Errorf("tool loop exceeded maximum steps")
}
