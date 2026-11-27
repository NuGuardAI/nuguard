package aiagent

import (
	"agent-desk/cmd/testdata/seedlang"
	"agent-desk/cmd/testdata/seeds"
	"regexp"
	"strings"
	"testing"
)

var hanTextPattern = regexp.MustCompile(`\p{Han}`)

func TestEnglishAIAgentSeedDoesNotContainChineseText(t *testing.T) {
	for _, item := range seeds.AIAgentSeeds(seedlang.English) {
		values := []string{item.Name, item.Description, item.SystemPrompt, item.WelcomeMessage, item.FallbackMessage}
		values = append(values, item.LegacyNames...)
		for _, value := range values {
			if hanTextPattern.MatchString(value) {
				t.Fatalf("english AI agent seed contains Chinese text: %q", value)
			}
		}
	}
}

func TestChineseAIAgentSeedUsesPresalesConfiguration(t *testing.T) {
	items := seeds.AIAgentSeeds(seedlang.Chinese)
	if len(items) != 1 {
		t.Fatalf("expected one Chinese AI agent seed, got %d", len(items))
	}

	item := items[0]
	if item.Name != "贝壳AI售前客服" {
		t.Fatalf("unexpected Chinese AI agent name: %q", item.Name)
	}
	for _, expected := range []string{
		"你是贝壳AI（AgentDesk）的售前客服",
		"# 售前对话策略",
		"# 事实与工具边界",
		"不得编造价格、折扣、合同条款、商业 SLA",
	} {
		if !strings.Contains(item.SystemPrompt, expected) {
			t.Fatalf("Chinese AI agent system prompt missing %q", expected)
		}
	}
	if !strings.Contains(item.WelcomeMessage, "贝壳AI售前客服") {
		t.Fatalf("unexpected welcome message: %q", item.WelcomeMessage)
	}
	if !strings.Contains(item.FallbackMessage, "方案评估") {
		t.Fatalf("unexpected fallback message: %q", item.FallbackMessage)
	}
}

func TestBuildModelsLeavesSkillsAndMCPToolsUnbound(t *testing.T) {
	items := buildModels(seedlang.Chinese, 7, []int64{11}, "13")
	if len(items) != 1 {
		t.Fatalf("expected one AI agent model, got %d", len(items))
	}

	item := items[0]
	if item.AIConfigID != 7 || item.KnowledgeIDs != "11" || item.TeamIDs != "13" {
		t.Fatalf("unexpected AI agent bindings: %+v", item)
	}
	if item.SkillIDs != "" {
		t.Fatalf("expected no Skill binding, got %q", item.SkillIDs)
	}
	if item.AllowedMCPTools != "" {
		t.Fatalf("expected no MCP Tool binding, got %q", item.AllowedMCPTools)
	}

	columns := seedUpdateColumns(item)
	if value, ok := columns["skill_ids"]; !ok || value != "" {
		t.Fatalf("seed update must clear Skill bindings, got %#v", value)
	}
	if value, ok := columns["allowed_mcp_tools"]; !ok || value != "" {
		t.Fatalf("seed update must clear MCP Tool bindings, got %#v", value)
	}
}
