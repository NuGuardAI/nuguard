package dashboard

import (
	"agent-desk/internal/pkg/httpx"
	"context"
	"strings"
	"time"

	"agent-desk/internal/builders"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func SkillDefinitionAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionSkillDefinitionView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	cnd := params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "status"},
		params.QueryFilter{ParamName: "name", Op: params.Like},
	).Desc("id")
	if _, ok := params.Get(ctx, "status"); !ok {
		cnd.Where("status <> ?", enums.StatusDeleted)
	}
	list, paging := services.SkillDefinitionService.FindPageByCnd(cnd)
	results := make([]response.SkillDefinitionResponse, 0, len(list))
	for _, item := range list {
		results = append(results, builders.BuildSkillDefinitionResponse(&item))
	}
	httpx.WriteJSON(ctx, &web.PageResult{Results: results, Page: paging})
}

func SkillDefinitionGetList_all(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionSkillDefinitionView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	cnd := params.NewSqlCnd(ctx,
		params.QueryFilter{ParamName: "status"},
	).Desc("id")
	if status, ok := params.Get(ctx, "status"); !ok || strings.TrimSpace(status) == "" {
		cnd.Where("status <> ?", enums.StatusDeleted)
	}
	list := services.SkillDefinitionService.Find(cnd)
	results := make([]response.SkillDefinitionResponse, 0, len(list))
	for _, item := range list {
		results = append(results, builders.BuildSkillDefinitionResponse(&item))
	}
	httpx.WriteJSON(ctx, results)
}

func SkillDefinitionGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionSkillDefinitionView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	item := services.SkillDefinitionService.Get(id)
	if item == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0053"))
		return
	}
	httpx.WriteJSON(ctx, builders.BuildSkillDefinitionResponse(item))
}

func SkillDefinitionPostCreate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionSkillDefinitionCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.CreateSkillDefinitionRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.SkillDefinitionService.CreateSkillDefinition(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildSkillDefinitionResponse(item))
}

func SkillDefinitionPostUpdate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionSkillDefinitionUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.UpdateSkillDefinitionRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.SkillDefinitionService.UpdateSkillDefinition(req, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func SkillDefinitionPostUpdate_status(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionSkillDefinitionUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.UpdateSkillDefinitionStatusRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if req.ID <= 0 {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0052"))
		return
	}
	if !enums.IsValidStatus(req.Status) {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0254"))
		return
	}
	item := services.SkillDefinitionService.Get(req.ID)
	if item == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0053"))
		return
	}
	if item.Status == enums.StatusDeleted {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0184"))
		return
	}
	if req.Status == int(enums.StatusDeleted) {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0319"))
		return
	}

	if err := services.SkillDefinitionService.Updates(req.ID, map[string]any{
		"status":           req.Status,
		"update_user_id":   operator.UserID,
		"update_user_name": operator.Username,
		"updated_at":       time.Now(),
	}); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func SkillDefinitionPostDelete(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionSkillDefinitionDelete)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.DeleteSkillDefinitionRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if req.ID <= 0 {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0052"))
		return
	}
	if services.SkillDefinitionService.Get(req.ID) == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0053"))
		return
	}
	if err := services.SkillDefinitionService.Updates(req.ID, map[string]any{
		"status":           enums.StatusDeleted,
		"update_user_id":   operator.UserID,
		"update_user_name": operator.Username,
		"updated_at":       time.Now(),
	}); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func SkillDefinitionPostRestore(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionSkillDefinitionDelete)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.RestoreSkillDefinitionRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if req.ID <= 0 {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0052"))
		return
	}

	item := services.SkillDefinitionService.Get(req.ID)
	if item == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0053"))
		return
	}
	if item.Status != enums.StatusDeleted {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0088"))
		return
	}

	if err := services.SkillDefinitionService.Updates(req.ID, map[string]any{
		"status":           enums.StatusDisabled,
		"update_user_id":   operator.UserID,
		"update_user_name": operator.Username,
		"updated_at":       time.Now(),
	}); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func SkillDefinitionPostDebug_run(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionSkillDefinitionView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.SkillDebugRunRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	resp, err := services.SkillRuntimeService.DebugRun(context.Background(), req)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, resp)
}

func SkillDefinitionPostDebug_resume(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionSkillDefinitionView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.SkillDebugResumeRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	resp, err := services.SkillRuntimeService.DebugResume(context.Background(), req)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, resp)
}
