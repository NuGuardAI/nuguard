package response

import (
	"encoding/json"
	"testing"
)

func TestAgentTeamScheduleResponseOmitsSourceType(t *testing.T) {
	payload, err := json.Marshal(AgentTeamScheduleResponse{
		ID:      1,
		TeamID:  2,
		StartAt: "2026-04-29 09:00:00",
		EndAt:   "2026-04-29 18:00:00",
		Remark:  "test",
	})
	if err != nil {
		t.Fatalf("marshal response error = %v", err)
	}

	var decoded map[string]any
	if err := json.Unmarshal(payload, &decoded); err != nil {
		t.Fatalf("unmarshal response error = %v", err)
	}
	if _, ok := decoded["sourceType"]; ok {
		t.Fatalf("sourceType should not be exposed: %s", payload)
	}
}
