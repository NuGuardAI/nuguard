package instruction

import (
	"agent-desk/internal/ai/runtime/tooling"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/toolx"
)

type ToolAppendixProvider struct{}

func NewToolAppendixProvider() *ToolAppendixProvider {
	return &ToolAppendixProvider{}
}

type SkillInstructionProvider struct{}

func NewSkillInstructionProvider() *SkillInstructionProvider {
	return &SkillInstructionProvider{}
}

func (p *SkillInstructionProvider) Resolve(selectedSkill *models.SkillDefinition) string {
	return BuildSelectedSkillActivationInstruction(selectedSkill)
}

func (p *ToolAppendixProvider) Build(toolDefinitions []tooling.MCPToolDefinition, extraToolCodes map[string]string) []string {
	appendixParts := make([]string, 0, 1)
	toolCodes := make([]string, 0, len(toolDefinitions)+len(extraToolCodes))
	for _, item := range toolDefinitions {
		toolCodes = append(toolCodes, item.ToolCode)
	}
	for _, item := range extraToolCodes {
		toolCodes = append(toolCodes, item)
	}
	appendixParts = append(appendixParts, toolx.BuildToolAppendicesForCodes(len(toolDefinitions) > 0, toolCodes)...)
	return appendixParts
}
