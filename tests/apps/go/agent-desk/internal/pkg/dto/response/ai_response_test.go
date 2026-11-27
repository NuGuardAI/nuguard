package response

import (
	"encoding/json"
	"testing"

	"agent-desk/internal/models"
)

func TestBuildAIConfigResponseOmitsAPIKey(t *testing.T) {
	payload, err := json.Marshal(BuildAIConfigResponse(&models.AIConfig{
		ID:     1,
		Name:   "test",
		APIKey: "sk-secret",
	}))
	if err != nil {
		t.Fatalf("marshal response error = %v", err)
	}

	var decoded map[string]any
	if err := json.Unmarshal(payload, &decoded); err != nil {
		t.Fatalf("unmarshal response error = %v", err)
	}
	if _, ok := decoded["apiKey"]; ok {
		t.Fatalf("apiKey should not be exposed: %s", payload)
	}
	if got, ok := decoded["hasApiKey"].(bool); !ok || !got {
		t.Fatalf("hasApiKey = %v, want true: %s", decoded["hasApiKey"], payload)
	}
}
