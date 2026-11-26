package readtools

import (
	"context"
	"testing"

	aitooling "agent-desk/internal/ai/tooling"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/toolx"
)

func TestExecuteGraphToolRejectsDisallowedToolBeforeGraphExecution(t *testing.T) {
	definition, _, err := ExecuteGraphTool(context.Background(), models.Conversation{}, toolx.GraphAnalyzeConversation.Code, map[string]any{
		"observedIssue": "需要分析的问题",
	}, aitooling.Policy{
		AllowedToolCodes:  []string{toolx.GraphPrepareTicketDraft.Code},
		AllowedRiskLevels: []string{aitooling.RiskLevelRead},
		Confirmed:         true,
	})
	if err == nil {
		t.Fatal("expected policy guard to reject the graph tool")
	}
	if definition.Code != toolx.GraphAnalyzeConversation.Code {
		t.Fatalf("definition code = %q, want %q", definition.Code, toolx.GraphAnalyzeConversation.Code)
	}
}
