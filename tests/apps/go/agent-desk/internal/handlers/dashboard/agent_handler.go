package dashboard

import (
	"agent-desk/internal/builders"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func AgentAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	list, paging := services.AgentProfileService.FindPageByCnd(params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "userId"},
		params.QueryFilter{ParamName: "teamId"},
		params.QueryFilter{ParamName: "serviceStatus"},
		params.QueryFilter{ParamName: "agentCode", Op: params.Like},
		params.QueryFilter{ParamName: "displayName", Op: params.Like},
	).Desc("id"))
	results := builders.BuildAgentProfileList(list)
	httpx.WriteJSON(ctx, &web.PageResult{Results: results, Page: paging})
}

func AgentGetList_all(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	list := services.AgentProfileService.Find(params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "userId"},
		params.QueryFilter{ParamName: "teamId"},
		params.QueryFilter{ParamName: "serviceStatus"},
		params.QueryFilter{ParamName: "agentCode", Op: params.Like},
	).Desc("id"))

	httpx.WriteJSON(ctx, builders.BuildAgentProfileList(list))
}

func AgentGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item := services.AgentProfileService.Get(id)
	if item == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0164"))
		return
	}
	httpx.WriteJSON(ctx, builders.BuildAgentProfileResponse(item))
}

func AgentPostCreate(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.CreateAgentProfileRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.AgentProfileService.CreateAgentProfile(req, user)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildAgentProfileResponse(item))
}

func AgentPostUpdate(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.UpdateAgentProfileRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AgentProfileService.UpdateAgentProfile(req, user); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AgentPostDelete(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentDelete); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.DeleteAgentProfileRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AgentProfileService.DeleteAgentProfile(req.ID); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}
