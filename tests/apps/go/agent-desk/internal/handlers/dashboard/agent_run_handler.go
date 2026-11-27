package dashboard

import (
	"agent-desk/internal/builders"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/pkg/httpx/params"
	"agent-desk/internal/services"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func AgentRunAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	queryParams := params.NewQueryParams(ctx)
	queryParams.Cnd = *params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "conversationId"},
		params.QueryFilter{ParamName: "aiAgentId"},
		params.QueryFilter{ParamName: "agentRevisionId"},
		params.QueryFilter{ParamName: "sourceMessageId"},
		params.QueryFilter{ParamName: "workflowRunId"},
		params.QueryFilter{ParamName: "status"},
	).Desc("id")
	list, paging := services.AgentRunService.FindPageByParams(queryParams)
	httpx.WriteJSON(ctx, &web.PageResult{Results: builders.BuildAgentRunList(list), Page: paging})
}

func AgentRunGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	run, steps, toolCalls := services.AgentRunService.GetDetail(id)
	if run == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0002"))
		return
	}
	httpx.WriteJSON(ctx, builders.BuildAgentRunDetail(run, steps, toolCalls, services.AgentRunService.GetQualityFeedback(run.ID)))
}

func AgentRunPostSave_quality_feedback(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.SaveAgentRunQualityFeedbackRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AgentRunService.SaveQualityFeedback(req, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AgentRunAnyMetrics(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	aiAgentID, _ := params.GetInt64(ctx, "aiAgentId")
	httpx.WriteJSON(ctx, services.AgentRunService.GetMetrics(aiAgentID))
}

func AgentRunPostEvaluate(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.RunAgentEvaluationRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	result, err := services.AgentEvaluationService.Run(ctx, req)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, result)
}
