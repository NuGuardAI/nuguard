package dashboard

import (
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func AIConfigAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIConfigView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	list, paging := services.AIConfigService.FindPageByCnd(params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "status"},
		params.QueryFilter{ParamName: "provider"},
		params.QueryFilter{ParamName: "modelType"},
		params.QueryFilter{ParamName: "name", Op: params.Like},
		params.QueryFilter{ParamName: "modelName", Op: params.Like},
	).Asc("sort_no").Desc("id"))
	results := make([]response.AIConfigResponse, 0, len(list))
	for _, item := range list {
		results = append(results, response.BuildAIConfigResponse(&item))
	}
	httpx.WriteJSON(ctx, &web.PageResult{Results: results, Page: paging})
}

func AIConfigAnyList_all(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIConfigView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	list := services.AIConfigService.Find(params.NewSqlCnd(ctx,
		params.QueryFilter{ParamName: "modelType"},
	).Eq("status", enums.StatusOk).Desc("sort_no").Desc("id"))

	results := make([]response.AIConfigResponse, 0, len(list))
	for _, item := range list {
		results = append(results, response.BuildAIConfigResponse(&item))
	}
	httpx.WriteJSON(ctx, results)
}

func AIConfigGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIConfigView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	item := services.AIConfigService.Get(id)
	if item == nil || item.Status == enums.StatusDeleted {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0012"))
		return
	}
	httpx.WriteJSON(ctx, response.BuildAIConfigResponse(item))
}

func AIConfigPostCreate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIConfigCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.CreateAIConfigRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.AIConfigService.CreateAIConfig(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, response.BuildAIConfigResponse(item))
}

func AIConfigPostUpdate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIConfigUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.UpdateAIConfigRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AIConfigService.UpdateAIConfig(req, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AIConfigPostDelete(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIConfigDelete)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.DeleteAIConfigRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AIConfigService.DeleteAIConfig(req.ID, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AIConfigPostUpdate_status(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIConfigUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.UpdateAIConfigStatusRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AIConfigService.UpdateStatus(req.ID, req.Status, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AIConfigPostUpdateSort(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAIConfigUpdate); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	var ids []int64
	if err := params.ReadJSON(ctx, &ids); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AIConfigService.UpdateSort(ids); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}
