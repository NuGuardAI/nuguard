package instruction

import (
	"strings"
	"testing"
)

func TestAssemblerRespectsProvidedSources(t *testing.T) {
	result := NewAssembler().Assemble(AssemblerInput{
		AgentInstruction: "agent-rule",
		SkillInstruction: "skill-rule",
		ToolAppendices:   []string{"tool-rule-1", "tool-rule-2"},
	})
	if !strings.Contains(result.Text, "Agent 规则：\nagent-rule") {
		t.Fatalf("missing agent instruction: %s", result.Text)
	}
	if !strings.Contains(result.Text, "当前技能上下文：\nskill-rule") {
		t.Fatalf("missing skill instruction: %s", result.Text)
	}
	if !strings.Contains(result.Text, "工具补充规则：\ntool-rule-1") {
		t.Fatalf("missing tool appendix: %s", result.Text)
	}
	if !result.Summary.HasAgentRule || !result.Summary.HasSkillRule || !result.Summary.HasToolRule {
		t.Fatalf("unexpected summary: %#v", result.Summary)
	}
}

func TestAssemblerReturnsEmptyTextWhenInputIsEmpty(t *testing.T) {
	result := NewAssembler().Assemble(AssemblerInput{})
	if result.Text != "" {
		t.Fatalf("expected empty assembled text, got: %s", result.Text)
	}
	if len(result.Summary.SectionTitles) != 0 || result.Summary.HasAgentRule || result.Summary.HasSkillRule || result.Summary.HasToolRule {
		t.Fatalf("expected empty summary, got %#v", result.Summary)
	}
}
