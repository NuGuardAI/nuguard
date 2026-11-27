package ai

import (
	"encoding/json"
	"testing"

	openai "github.com/openai/openai-go/v3"
	"github.com/openai/openai-go/v3/shared"

	"agent-desk/internal/models"
)

func TestApplyProviderSpecificChatParamsIncludesDashScopeThinkingFlag(t *testing.T) {
	params := openai.ChatCompletionNewParams{
		Messages: []openai.ChatCompletionMessageParamUnion{
			{
				OfUser: &openai.ChatCompletionUserMessageParam{
					Content: openai.ChatCompletionUserMessageParamContentUnion{
						OfString: openai.String("hello"),
					},
				},
			},
		},
		Model: shared.ChatModel("qwen3.5-plus"),
	}

	applyProviderSpecificChatParams(&params, models.AIConfig{
		BaseURL:   "https://dashscope.aliyuncs.com/compatible-mode/v1",
		ModelName: "qwen3.5-plus",
	})

	raw, err := json.Marshal(params)
	if err != nil {
		t.Fatalf("marshal params: %v", err)
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatalf("unmarshal params: %v", err)
	}
	if got, ok := body["enable_thinking"].(bool); !ok || got {
		t.Fatalf("expected enable_thinking=false in request body, got body=%s", raw)
	}
}
