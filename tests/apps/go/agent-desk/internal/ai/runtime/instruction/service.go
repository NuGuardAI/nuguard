package instruction

import (
	"strings"

	runtimetooling "agent-desk/internal/ai/runtime/tooling"
	"agent-desk/internal/models"
)

type Service struct {
	assembler                *Assembler
	skillInstructionProvider *SkillInstructionProvider
	toolAppendixProvider     *ToolAppendixProvider
}

func NewService(
	assembler *Assembler,
	skillProvider *SkillInstructionProvider,
	toolProvider *ToolAppendixProvider,
) *Service {
	if assembler == nil {
		assembler = NewAssembler()
	}
	if skillProvider == nil {
		skillProvider = NewSkillInstructionProvider()
	}
	if toolProvider == nil {
		toolProvider = NewToolAppendixProvider()
	}
	return &Service{
		assembler:                assembler,
		skillInstructionProvider: skillProvider,
		toolAppendixProvider:     toolProvider,
	}
}

func (s *Service) Build(
	aiAgent models.AIAgent,
	selectedSkill *models.SkillDefinition,
	toolDefinitions []runtimetooling.MCPToolDefinition,
	extraToolCodes map[string]string,
) AssemblyResult {
	skillInstruction := ""
	toolAppendices := make([]string, 0)
	if s != nil && s.skillInstructionProvider != nil {
		skillInstruction = s.skillInstructionProvider.Resolve(selectedSkill)
	}
	if s != nil && s.toolAppendixProvider != nil {
		toolAppendices = s.toolAppendixProvider.Build(toolDefinitions, extraToolCodes)
	}
	assembler := NewAssembler()
	if s != nil && s.assembler != nil {
		assembler = s.assembler
	}
	return assembler.Assemble(AssemblerInput{
		AgentInstruction: strings.TrimSpace(aiAgent.SystemPrompt),
		SkillInstruction: skillInstruction,
		ToolAppendices:   toolAppendices,
	})
}
