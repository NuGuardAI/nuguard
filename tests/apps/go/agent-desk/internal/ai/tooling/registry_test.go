package tooling

import (
	"strings"
	"testing"

	"agent-desk/internal/pkg/toolx"
)

func TestRegistryResolvesRegisteredToolPolicy(t *testing.T) {
	definition, err := DefaultRegistry.Resolve(toolx.GraphCreateTicketConfirm.Code)
	if err != nil {
		t.Fatalf("Resolve returned error: %v", err)
	}
	if definition.RiskLevel != RiskLevelWrite || !definition.RequireConfirmation || definition.MaxCallsPerRun != 1 {
		t.Fatalf("unexpected definition: %#v", definition)
	}
	if err := DefaultRegistry.Authorize(definition, Policy{AllowedToolCodes: []string{toolx.GraphCreateTicketConfirm.Code}}); err == nil {
		t.Fatal("expected confirmation requirement")
	}
}

func TestRegistryIncludesGraphInputSchemaAndRiskPolicy(t *testing.T) {
	definition, err := DefaultRegistry.Resolve(toolx.GraphCreateTicketConfirm.Code)
	if err != nil {
		t.Fatalf("Resolve returned error: %v", err)
	}
	if definition.InputSchema["type"] != "object" || len(definition.InputSchema["required"].([]string)) != 2 {
		t.Fatalf("unexpected graph schema: %#v", definition.InputSchema)
	}
	if err := DefaultPolicyGuard.Authorize(Invocation{Definition: definition, Policy: Policy{
		AllowedToolCodes: []string{definition.Code}, AllowedRiskLevels: []string{RiskLevelRead}, Confirmed: true,
	}}); err == nil || !strings.Contains(err.Error(), "risk level") {
		t.Fatalf("expected risk policy rejection, got %v", err)
	}
}

func TestRegistryRequiresConfirmationForHandoff(t *testing.T) {
	definition, err := DefaultRegistry.Resolve(toolx.GraphHandoffConversation.Code)
	if err != nil {
		t.Fatalf("Resolve returned error: %v", err)
	}
	if definition.RiskLevel != RiskLevelWrite || !definition.RequireConfirmation || definition.IdempotencyMode != "business" {
		t.Fatalf("unexpected handoff policy: %#v", definition)
	}
	if err := DefaultRegistry.Authorize(definition, Policy{AllowedToolCodes: []string{definition.Code}, AllowedRiskLevels: []string{RiskLevelWrite}}); err == nil || !strings.Contains(err.Error(), "confirmation") {
		t.Fatalf("expected handoff confirmation rejection, got %v", err)
	}
}

func TestRegistryIncludesAllTicketDraftToolInputs(t *testing.T) {
	definition, err := DefaultRegistry.Resolve(toolx.GraphPrepareTicketDraft.Code)
	if err != nil {
		t.Fatalf("Resolve returned error: %v", err)
	}
	properties, _ := definition.InputSchema["properties"].(map[string]any)
	for _, key := range []string{"title", "description", "issue", "impact", "expectedOutcome", "currentAttempt"} {
		if _, ok := properties[key]; !ok {
			t.Fatalf("ticket draft schema missing %q: %#v", key, definition.InputSchema)
		}
	}
}

func TestRegistryTreatsAdministratorSelectedMCPToolsAsAllowedTools(t *testing.T) {
	definition, err := DefaultRegistry.Resolve("knowledge/search")
	if err != nil {
		t.Fatalf("Resolve returned error: %v", err)
	}
	if definition.RiskLevel != RiskLevelRead || definition.RequireConfirmation {
		t.Fatalf("unexpected MCP definition: %#v", definition)
	}
	if err := DefaultRegistry.Authorize(definition, Policy{AllowedToolCodes: []string{"knowledge/search"}, AllowedRiskLevels: []string{RiskLevelRead}}); err != nil {
		t.Fatalf("Authorize returned error: %v", err)
	}
}

func TestSanitizePreviewMasksAndBoundsSecrets(t *testing.T) {
	preview := SanitizePreview(`authorization=Bearer-secret {"token":"abc123"}`)
	if strings.Contains(preview, "Bearer-secret") || strings.Contains(preview, "abc123") {
		t.Fatalf("secret leaked in preview: %q", preview)
	}
}

func TestNormalizeCustomerReplyRejectsSecretAndNormalizesText(t *testing.T) {
	if _, err := NormalizeCustomerReply("token=abc123"); err == nil {
		t.Fatal("expected sensitive reply to be rejected")
	}
	reply, err := NormalizeCustomerReply("  first\x00\n\n\n\nsecond  ")
	if err != nil || reply != "first\n\nsecond" {
		t.Fatalf("unexpected normalized reply: %q err=%v", reply, err)
	}
}

func TestMCPExecutorAllowsSelectedToolThroughAuthorization(t *testing.T) {
	executor := NewMCPExecutor(DefaultRegistry, nil)
	_, _, err := executor.Execute(t.Context(), "knowledge/search", nil, Policy{
		AllowedToolCodes:  []string{"knowledge/search"},
		AllowedRiskLevels: []string{RiskLevelRead},
	})
	if err == nil || !strings.Contains(err.Error(), "runtime is not configured") {
		t.Fatalf("expected authorization to pass before the missing runtime error, got %v", err)
	}
}

func TestPolicyGuardRejectsTotalCallsAndOversizedArguments(t *testing.T) {
	definition, err := DefaultRegistry.Resolve("knowledge/search")
	if err != nil {
		t.Fatalf("Resolve returned error: %v", err)
	}
	if err := DefaultPolicyGuard.Authorize(Invocation{
		Definition: definition,
		Policy:     Policy{AllowedToolCodes: []string{definition.Code}, Confirmed: true, TotalCallCount: 2, MaxTotalCalls: 2},
	}); err == nil || !strings.Contains(err.Error(), "total") {
		t.Fatalf("expected total call rejection, got %v", err)
	}
	if err := DefaultPolicyGuard.Authorize(Invocation{
		Definition: definition, Arguments: map[string]any{"query": strings.Repeat("x", 40)},
		Policy: Policy{AllowedToolCodes: []string{definition.Code}, Confirmed: true, MaxArgumentBytes: 16},
	}); err == nil || !strings.Contains(err.Error(), "size") {
		t.Fatalf("expected argument size rejection, got %v", err)
	}
}

func TestPolicyGuardRejectsToolOutsideSelectedSkillWhitelist(t *testing.T) {
	definition, err := DefaultRegistry.Resolve("knowledge/search")
	if err != nil {
		t.Fatalf("Resolve returned error: %v", err)
	}
	err = DefaultPolicyGuard.Authorize(Invocation{
		Definition: definition,
		Policy: Policy{
			AllowedToolCodes:      []string{"knowledge/search"},
			SkillAllowedToolCodes: []string{"customer/profile"},
			Confirmed:             true,
		},
	})
	if err == nil || !strings.Contains(err.Error(), "selected skill") {
		t.Fatalf("expected skill whitelist rejection, got %v", err)
	}
}
