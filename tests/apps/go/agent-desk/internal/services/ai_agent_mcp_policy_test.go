package services

import (
	"testing"

	"agent-desk/internal/pkg/dto/request"
)

func TestValidateMCPToolRiskPolicyRejectsTrustedToolOverride(t *testing.T) {
	_, err := validateMCPToolRiskPolicy(request.AIAgentMCPToolRequest{
		ToolCode:            "system/server_time",
		RiskLevel:           "write",
		RequireConfirmation: true,
	})
	if err == nil {
		t.Fatal("expected trusted system tool policy override to be rejected")
	}

	item, err := validateMCPToolRiskPolicy(request.AIAgentMCPToolRequest{
		ToolCode:  "system/server_time",
		RiskLevel: "read",
	})
	if err != nil {
		t.Fatalf("validate trusted system tool policy: %v", err)
	}
	if item.Title != "获取当前时间" || item.RiskLevel != "read" || item.RequireConfirmation {
		t.Fatalf("unexpected normalized trusted policy: %#v", item)
	}
}

func TestValidateMCPToolRiskPolicyRequiresWriteConfirmation(t *testing.T) {
	_, err := validateMCPToolRiskPolicy(request.AIAgentMCPToolRequest{
		ToolCode:  "crm/update_customer",
		RiskLevel: "write",
	})
	if err == nil {
		t.Fatal("expected write tool without confirmation to be rejected")
	}
}
