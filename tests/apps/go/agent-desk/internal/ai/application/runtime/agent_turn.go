package runtime

import (
	"context"
	"fmt"
	"strings"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/toolx"
	"agent-desk/internal/pkg/utils"
	svc "agent-desk/internal/services"
)

type agentLoopTurn struct {
	RetrieverCount int
	RetrieveErr    error
	ResponsePolicy agentLoopResponsePolicy
	SystemPrompt   string
	UserPrompt     string
	HistoryCount   int
	AllowedTools   []string
	ToolPolicy     agentLoopToolPolicy
	Skills         map[int64]models.SkillDefinition
	Workflows      map[int64]svc.AgentRevisionWorkflowBinding
}

func (e *AgentLoopEngine) prepareTurn(ctx context.Context, req RunInput, snapshot *svc.AgentRevisionSnapshot) agentLoopTurn {
	knowledgeContext, retrieverCount, retrieveErr := e.retrieveKnowledge(ctx, req.AIAgent, req.UserMessage.Content)
	responsePolicy := evaluateAgentLoopResponsePolicy(req.AIAgent, knowledgeContext, retrieveErr)
	systemPrompt := buildAgentLoopSystemPrompt(req.AIAgent, len(utils.SplitInt64s(req.AIAgent.KnowledgeIDs)) > 0, knowledgeContext, retrieveErr)
	userPrompt, historyCount := e.buildUserPrompt(req)
	if knowledgeContext != "" {
		userPrompt += "\n\nKnowledge evidence:\n" + knowledgeContext
	}
	skills := svc.SkillDefinitionService.GetByIDs(utils.SplitInt64s(req.AIAgent.SkillIDs))
	workflows := make(map[int64]svc.AgentRevisionWorkflowBinding, len(snapshot.WorkflowBindings))
	allowedTools := agentLoopSafeBuiltinCodes()
	// TODO 这么实现我觉得不太好，最好是能够有个统一的能力目录
	catalog := []string{
		"- " + toolx.BuiltinConversationContext.Code + " | Builtin | 读取当前会话和客户上下文",
		"- " + toolx.BuiltinKnowledgeRetrieve.Code + " | Builtin | 按需再次检索已绑定知识库",
		"- " + toolx.GraphTriageServiceRequest.Code + " | Builtin | 分析服务请求并生成处置建议",
		"- " + toolx.GraphAnalyzeConversation.Code + " | Builtin | 分析会话意图和风险信号",
		"- " + toolx.GraphPrepareTicketDraft.Code + " | Builtin | 只生成工单草稿，不执行写入",
	}
	for id, skill := range skills {
		if skill.Status != enums.StatusOk {
			continue
		}
		code := agentLoopSkillCode(id)
		allowedTools = append(allowedTools, code)
		catalog = append(catalog, fmt.Sprintf("- %s | Skill | %s | %s", code, strings.TrimSpace(skill.Name), strings.TrimSpace(skill.Description)))
	}
	for _, binding := range snapshot.WorkflowBindings {
		if binding.WorkflowVersionID <= 0 {
			continue
		}
		workflows[binding.WorkflowVersionID] = binding
		code := agentLoopWorkflowCode(binding.WorkflowVersionID)
		allowedTools = append(allowedTools, code)
		catalog = append(catalog, fmt.Sprintf("- %s | Workflow | %s | %s", code, strings.TrimSpace(binding.ToolName), strings.TrimSpace(binding.TriggerInstruction)))
	}
	mcpTools, _ := toolx.ParseAgentMCPToolsJSON(req.AIAgent.AllowedMCPTools)
	for _, tool := range mcpTools {
		if strings.TrimSpace(tool.ToolCode) == "" {
			continue
		}
		allowedTools = append(allowedTools, tool.ToolCode)
		catalog = append(catalog, fmt.Sprintf("- %s | MCP | %s | %s", tool.ToolCode, tool.Title, tool.Description))
	}
	systemPrompt += "\n\nAvailable capabilities:\n" + strings.Join(catalog, "\n")
	systemPrompt += "\n\nUse tool_search with an exact capability code only when needed. You decide whether to answer directly, activate a Skill, execute a Workflow, retrieve knowledge, or call MCP. A Skill activation returns instructions for this same run. Never invent a capability code. For any requested internal action such as human handoff, call conversation_decision; its action is a structured proposal only, and the runtime performs the action. When the customer explicitly asks for human support, set action=handoff, handoffInitiator=customer, and handoffConfirmed=true; do not ask again. Use ask_handoff_confirmation only when you, not the customer, recommend an unconfirmed handoff, with handoffInitiator=agent and handoffConfirmed=false. Never claim a handoff, assignment, or queue entry succeeded in reply text."
	return agentLoopTurn{
		RetrieverCount: retrieverCount,
		RetrieveErr:    retrieveErr,
		ResponsePolicy: responsePolicy,
		SystemPrompt:   systemPrompt,
		UserPrompt:     userPrompt,
		HistoryCount:   historyCount,
		AllowedTools:   allowedTools,
		ToolPolicy:     parseAgentLoopToolPolicy(req.AIAgent.ToolPolicy),
		Skills:         skills,
		Workflows:      workflows,
	}
}
