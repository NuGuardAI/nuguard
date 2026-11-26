package ai

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
)

func TestChatWithToolsExecutesToolAndContinuesConversation(t *testing.T) {
	var requestCount atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path != "/v1/chat/completions" {
			t.Fatalf("unexpected path: %s", r.URL.Path)
		}
		var body struct {
			Messages []struct {
				Role      string `json:"role"`
				Content   string `json:"content"`
				ToolCalls []struct {
					ID string `json:"id"`
				} `json:"tool_calls"`
			} `json:"messages"`
			Tools []json.RawMessage `json:"tools"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("decode request: %v", err)
		}

		switch requestCount.Add(1) {
		case 1:
			if len(body.Tools) != 1 || len(body.Messages) != 2 || body.Messages[1].Role != "user" {
				t.Fatalf("unexpected first request: %+v", body)
			}
			_, _ = w.Write([]byte(`{"id":"chatcmpl-1","object":"chat.completion","created":1,"model":"test-model","choices":[{"index":0,"message":{"role":"assistant","content":"","tool_calls":[{"id":"call-1","type":"function","function":{"name":"lookup","arguments":"{\"q\":\"refund\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}`))
		case 2:
			if len(body.Messages) != 4 || body.Messages[2].Role != "assistant" || len(body.Messages[2].ToolCalls) != 1 || body.Messages[3].Role != "tool" || body.Messages[3].Content != "refund policy" {
				t.Fatalf("tool result was not continued in second request: %+v", body.Messages)
			}
			_, _ = w.Write([]byte(`{"id":"chatcmpl-2","object":"chat.completion","created":2,"model":"test-model","choices":[{"index":0,"message":{"role":"assistant","content":"Refunds are available within 30 days."},"finish_reason":"stop"}],"usage":{"prompt_tokens":20,"completion_tokens":4,"total_tokens":24}}`))
		default:
			t.Fatalf("unexpected extra request")
		}
	}))
	defer server.Close()

	var executed ToolCall
	result, err := LLM.ChatWithTools(context.Background(), models.AIConfig{
		Provider:  enums.AIProviderOpenAI,
		BaseURL:   server.URL + "/v1",
		APIKey:    "test-key",
		ModelName: "test-model",
	}, "You are helpful.", "What is the refund policy?", []ToolDefinition{{
		Name:        "lookup",
		Description: "Look up a policy.",
		Parameters:  map[string]any{"type": "object"},
	}}, 3, func(_ context.Context, call ToolCall) (string, error) {
		executed = call
		return "refund policy", nil
	})
	if err != nil {
		t.Fatalf("ChatWithTools: %v", err)
	}
	if got, want := result.Content, "Refunds are available within 30 days."; got != want {
		t.Fatalf("result content = %q, want %q", got, want)
	}
	if executed.Name != "lookup" || executed.ID != "call-1" || executed.Arguments != `{"q":"refund"}` {
		t.Fatalf("executed tool call = %+v", executed)
	}
	if len(result.ToolCalls) != 1 || result.PromptTokens != 20 || result.CompletionTokens != 4 {
		t.Fatalf("unexpected result: %+v", result)
	}
	if got := requestCount.Load(); got != 2 {
		t.Fatalf("request count = %d, want 2", got)
	}
}
