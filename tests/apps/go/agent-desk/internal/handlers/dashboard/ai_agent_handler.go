package dashboard

import (
	"agent-desk/internal/builders"
	"agent-desk/internal/pkg/httpx"
	"encoding/json"
	"strings"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/i18nx"
	"agent-desk/internal/pkg/toolx"
	"agent-desk/internal/pkg/utils"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/sqls"
	"github.com/mlogclub/simple/web"
)

func AIAgentAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	cnd := params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "status"},
		params.QueryFilter{ParamName: "name", Op: params.Like},
		params.QueryFilter{ParamName: "code", Op: params.Like},
	).Asc("sort_no").Desc("id")
	list, paging := services.AIAgentService.FindPageByCnd(cnd)
	results := make([]response.AIAgentResponse, 0, len(list))
	for _, item := range list {
		results = append(results, buildAIAgentResponseWithLocale(&item, i18nx.Locale(ctx)))
	}
	httpx.WriteJSON(ctx, &web.PageResult{Results: results, Page: paging})
}

func AIAgentGetList_all(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	list := services.AIAgentService.Find(sqls.NewCnd().Where("status = ?", enums.StatusOk).Desc("sort_no").Desc("id"))
	results := make([]response.AIAgentResponse, 0, len(list))
	for _, item := range list {
		results = append(results, buildAIAgentResponseWithLocale(&item, i18nx.Locale(ctx)))
	}
	httpx.WriteJSON(ctx, results)
}

func AIAgentGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item := services.AIAgentService.Get(id)
	if item == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0002"))
		return
	}
	httpx.WriteJSON(ctx, buildAIAgentResponseWithLocale(item, i18nx.Locale(ctx)))
}

func AIAgentPostCreate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.CreateAIAgentRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.AIAgentService.CreateAIAgent(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, buildAIAgentResponseWithLocale(item, i18nx.Locale(ctx)))
}

func AIAgentPostUpdate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.UpdateAIAgentRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AIAgentService.UpdateAIAgent(req, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AIAgentPostDelete(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentDelete)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.DeleteAIAgentRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AIAgentService.DeleteAIAgent(req.ID, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AIAgentPostPublish(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.PublishAIAgentRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	_, err = services.AIAgentService.PublishAIAgent(req.ID, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AIAgentAnyRevisionList(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if services.AIAgentService.Get(id) == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0002"))
		return
	}
	httpx.WriteJSON(ctx, builders.BuildAgentRevisionList(services.AgentRevisionService.FindByAgentID(id)))
}

func AIAgentPostRollback(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.RollbackAIAgentRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AIAgentService.RollbackAIAgent(req.ID, req.RevisionID, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AIAgentPostRollback_rollout(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.RollbackAIAgentRolloutRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AIAgentService.RollbackAIAgentRollout(req.ID, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AIAgentPostUpdate_sort(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentUpdate); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	var ids []int64
	if err := params.ReadJSON(ctx, &ids); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AIAgentService.UpdateSort(ids); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AIAgentPostUpdate_status(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.UpdateAIAgentStatusRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AIAgentService.UpdateStatus(req.ID, req.Status, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func buildAIAgentResponse(item *models.AIAgent) response.AIAgentResponse {
	return buildAIAgentResponseWithLocale(item, i18nx.DefaultLocale)
}

func buildAIAgentResponseWithLocale(item *models.AIAgent, locale string) response.AIAgentResponse {
	ret := response.AIAgentResponse{
		ID:                     item.ID,
		Name:                   item.Name,
		Description:            item.Description,
		Status:                 item.Status,
		StatusName:             enums.GetStatusLabel(item.Status),
		AIConfigID:             item.AIConfigID,
		MaxSteps:               item.MaxSteps,
		ContextWindow:          item.ContextWindow,
		ToolPolicy:             item.ToolPolicy,
		KnowledgePolicy:        item.KnowledgePolicy,
		ServiceMode:            item.ServiceMode,
		ServiceModeName:        enums.GetIMConversationServiceModeLabel(item.ServiceMode),
		SystemPrompt:           item.SystemPrompt,
		WelcomeMessage:         item.WelcomeMessage,
		ReplyTimeoutSeconds:    item.ReplyTimeoutSeconds,
		RolloutPercent:         item.RolloutPercent,
		PreviousRolloutPercent: item.PreviousRolloutPercent,
		HandoffMode:            item.HandoffMode,
		HandoffModeName:        enums.GetAIAgentHandoffModeLabel(item.HandoffMode),
		FallbackMode:           item.FallbackMode,
		FallbackModeName:       enums.GetAIAgentFallbackModeLabel(item.FallbackMode),
		FallbackMessage:        item.FallbackMessage,
		KnowledgeBaseIDs:       utils.SplitInt64s(item.KnowledgeIDs),
		SkillIDs:               utils.SplitInt64s(item.SkillIDs),
		Skills:                 make([]response.AIAgentSkillResponse, 0),
		Teams:                  make([]response.AIAgentTeamResponse, 0),
		MCPTools:               make([]response.AIAgentMCPToolResponse, 0),
		WorkflowBindings:       make([]response.AIAgentWorkflowBindingResponse, 0),
		PublishedRevisionID:    item.PublishedRevisionID,
		SortNo:                 item.SortNo,
		CreatedAt:              item.CreatedAt.Format("2006-01-02 15:04:05"),
		UpdatedAt:              item.UpdatedAt.Format("2006-01-02 15:04:05"),
		CreateUserName:         item.CreateUserName,
		UpdateUserName:         item.UpdateUserName,
	}
	if aiConfig := services.AIConfigService.Get(item.AIConfigID); aiConfig != nil {
		ret.AIConfigName = aiConfig.Name
	}
	for _, id := range utils.SplitInt64s(item.TeamIDs) {
		if team := services.AgentTeamService.Get(id); team != nil {
			ret.Teams = append(ret.Teams, response.AIAgentTeamResponse{
				ID:   team.ID,
				Name: team.Name,
			})
		}
	}
	for _, id := range ret.SkillIDs {
		if skill := services.SkillDefinitionService.Get(id); skill != nil {
			ret.Skills = append(ret.Skills, response.AIAgentSkillResponse{
				ID:   skill.ID,
				Name: skill.Name,
			})
		}
	}
	if raw := strings.TrimSpace(item.AllowedMCPTools); raw != "" {
		var mcpTools []request.AIAgentMCPToolRequest
		if err := json.Unmarshal([]byte(raw), &mcpTools); err == nil {
			for _, tool := range mcpTools {
				toolCode := strings.TrimSpace(tool.ToolCode)
				if toolCode == "" {
					toolCode = toolx.BuildMCPToolCode(tool.ServerCode, tool.ToolName)
				}
				toolCode = toolx.NormalizeToolCodeAlias(toolCode)
				if toolx.ResolveToolSourceType(toolCode) != enums.ToolSourceTypeMCP {
					continue
				}
				serverCode := strings.TrimSpace(tool.ServerCode)
				toolName := strings.TrimSpace(tool.ToolName)
				if registeredServerCode, registeredToolName, ok := toolx.GetRegisteredToolIdentity(toolCode); ok {
					serverCode = registeredServerCode
					toolName = registeredToolName
				} else if parsedServerCode, parsedToolName := toolx.SplitMCPToolCode(toolCode); parsedServerCode != "" && parsedToolName != "" {
					serverCode = parsedServerCode
					toolName = parsedToolName
				}
				title := strings.TrimSpace(tool.Title)
				if title == "" {
					if registeredTitle := toolx.GetRegisteredToolTitleLocale(toolCode, locale); registeredTitle != "" {
						title = registeredTitle
					}
				}
				description := strings.TrimSpace(tool.Description)
				if description == "" {
					if registeredDescription := toolx.GetRegisteredToolDescriptionLocale(toolCode, locale); registeredDescription != "" {
						description = registeredDescription
					}
				}
				ret.MCPTools = append(ret.MCPTools, response.AIAgentMCPToolResponse{
					ToolCode:            toolCode,
					ServerCode:          serverCode,
					ToolName:            toolName,
					Title:               title,
					Description:         description,
					RiskLevel:           tool.RiskLevel,
					RequireConfirmation: tool.RequireConfirmation,
					Arguments:           tool.Arguments,
				})
			}
		}
	}
	for _, binding := range services.AIAgentService.ListWorkflowBindings(item.ID) {
		if binding.Workflow == nil || binding.Version == nil {
			continue
		}
		ret.WorkflowBindings = append(ret.WorkflowBindings, response.AIAgentWorkflowBindingResponse{
			ID: binding.Binding.ID, WorkflowID: binding.Binding.WorkflowID, WorkflowVersionID: binding.Binding.WorkflowVersionID,
			WorkflowName: binding.Workflow.Name, WorkflowVersion: binding.Version.Version, ToolName: binding.Binding.ToolName,
			TriggerInstruction: binding.Binding.TriggerInstruction, Priority: binding.Binding.Priority, Enabled: binding.Binding.Enabled,
		})
	}
	return ret
}
