package dashboard

import (
	"agent-desk/internal/builders"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/pkg/httpx/params"
	"agent-desk/internal/services"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func AIWorkflowAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	cnd := params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "status"},
		params.QueryFilter{ParamName: "name", Op: params.Like},
	).NotEq("status", enums.StatusDeleted).Desc("id")
	list, paging := services.AIWorkflowService.FindPageByCnd(cnd)
	httpx.WriteJSON(ctx, &web.PageResult{Results: builders.BuildAIWorkflowList(list), Page: paging})
}

func AIWorkflowGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item := services.AIWorkflowService.Get(id)
	if item == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0002"))
		return
	}
	httpx.WriteJSON(ctx, builders.BuildAIWorkflow(item))
}

func AIWorkflowPostCreate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.CreateAIWorkflowRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.AIWorkflowService.CreateWorkflow(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildAIWorkflow(item))
}

func AIWorkflowPostUpdate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.UpdateAIWorkflowRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AIWorkflowService.UpdateWorkflow(req, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AIWorkflowPostDelete(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentDelete)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.DeleteAIWorkflowRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AIWorkflowService.DeleteWorkflow(req.ID, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AIWorkflowPostRestoreVersion(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.RestoreAIWorkflowVersionRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AIWorkflowService.RestoreVersion(req, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AIWorkflowGetUsage(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	items := services.AIWorkflowService.ListUsage(id)
	ret := make([]response.AIWorkflowUsageResponse, 0, len(items))
	for _, item := range items {
		if item.Agent == nil || item.Version == nil {
			continue
		}
		ret = append(ret, response.AIWorkflowUsageResponse{AIAgentID: item.Agent.ID, AIAgentName: item.Agent.Name, WorkflowVersionID: item.Binding.WorkflowVersionID, WorkflowVersion: item.Version.Version, Enabled: item.Binding.Enabled})
	}
	httpx.WriteJSON(ctx, ret)
}

func AIWorkflowGetNodeSpecList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildAIWorkflowNodeSpecs(services.AIWorkflowService.ListNodeSpecs()))
}

func AIWorkflowGetDefaultDefinition(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, services.AIWorkflowService.DefaultAgentWorkflowDefinition())
}

func AIWorkflowGetTemplateList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildAIWorkflowTemplates(services.AIWorkflowService.ListWorkflowTemplates()))
}

func AIWorkflowPostValidate(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.ValidateAIWorkflowRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	result := services.AIWorkflowService.ValidateDefinition(req.Definition)
	httpx.WriteJSON(ctx, response.AIWorkflowValidationResponse{
		Valid:  result.Valid,
		Errors: result.Errors,
	})
}

func AIWorkflowPostPublish(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.PublishAIWorkflowRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.AIWorkflowService.PublishWorkflow(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildAIWorkflowVersion(item))
}

func AIWorkflowAnyVersionList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	queryParams := params.NewQueryParams(ctx)
	cnd := params.NewPagedSqlCnd(ctx, params.QueryFilter{ParamName: "workflowId"}).Desc("version").Desc("id")
	queryParams.Cnd = *cnd
	list, paging := services.AIWorkflowService.FindVersionPageByParams(queryParams)
	httpx.WriteJSON(ctx, &web.PageResult{Results: builders.BuildAIWorkflowVersionList(list), Page: paging})
}

func AIWorkflowGetVersionBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item := services.AIWorkflowService.GetVersion(id)
	if item == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0002"))
		return
	}
	httpx.WriteJSON(ctx, builders.BuildAIWorkflowVersion(item))
}

func AIWorkflowAnyRunList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	cnd := params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "workflowId"},
		params.QueryFilter{ParamName: "workflowVersionId"},
		params.QueryFilter{ParamName: "conversationId"},
		params.QueryFilter{ParamName: "aiAgentId"},
		params.QueryFilter{ParamName: "messageId"},
		params.QueryFilter{ParamName: "status"},
	).Desc("id")
	list, paging := services.AIWorkflowService.FindRunPageByCnd(cnd)
	auditItems := services.AIWorkflowService.BuildRunAuditItems(list)
	results := make([]response.AIWorkflowRunResponse, 0, len(auditItems))
	for i := range auditItems {
		item := auditItems[i]
		results = append(results, builders.BuildAIWorkflowRunWithContext(&item.Run, item.Workflow, item.Version, item.Agent))
	}
	httpx.WriteJSON(ctx, &web.PageResult{Results: results, Page: paging})
}

func AIWorkflowGetRunBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, nodes := services.AIWorkflowService.GetRunDetail(id)
	if item == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0002"))
		return
	}
	auditItems := services.AIWorkflowService.BuildRunAuditItems([]models.AIWorkflowRun{*item})
	if len(auditItems) == 0 {
		httpx.WriteJSON(ctx, builders.BuildAIWorkflowRunDetail(item, nodes))
		return
	}
	auditItem := auditItems[0]
	httpx.WriteJSON(ctx, builders.BuildAIWorkflowRunDetailWithContext(&auditItem.Run, nodes, auditItem.Workflow, auditItem.Version, auditItem.Agent))
}
