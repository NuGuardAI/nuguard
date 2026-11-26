package dashboard

import (
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/sqls"
	"github.com/mlogclub/simple/web"
)

func RoleAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionRoleView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	cnd := params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "status"},
		params.QueryFilter{ParamName: "code", Op: params.Like},
	).Asc("sort_no").Desc("id")
	list, paging := services.RoleService.FindPageByCnd(cnd)
	results := make([]response.RoleResponse, 0, len(list))
	for _, item := range list {
		results = append(results, response.RoleResponse{
			ID:       item.ID,
			Name:     item.Name,
			Code:     item.Code,
			Status:   item.Status,
			IsSystem: item.IsSystem,
			SortNo:   item.SortNo,
		})
	}
	httpx.WriteJSON(ctx, &web.PageResult{Results: results, Page: paging})
}

func RoleGetList_all(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionRoleView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	list := services.RoleService.Find(sqls.NewCnd().Asc("sort_no").Desc("id"))
	results := make([]response.RoleResponse, 0, len(list))
	for _, item := range list {
		results = append(results, response.RoleResponse{
			ID:       item.ID,
			Name:     item.Name,
			Code:     item.Code,
			Status:   item.Status,
			IsSystem: item.IsSystem,
			SortNo:   item.SortNo,
		})
	}
	httpx.WriteJSON(ctx, results)
}

func RoleGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionRoleView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	item := services.RoleService.Get(id)
	if item == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0305"))
		return
	}

	permissionCodes := make([]string, 0)
	list := services.RolePermissionService.Find(sqls.NewCnd().Eq("role_id", item.ID))
	for _, relation := range list {
		permission := services.PermissionService.Get(relation.PermissionID)
		if permission != nil {
			permissionCodes = append(permissionCodes, permission.Code)
		}
	}
	httpx.WriteJSON(ctx, &response.RoleResponse{
		ID:          item.ID,
		Name:        item.Name,
		Code:        item.Code,
		Status:      item.Status,
		IsSystem:    item.IsSystem,
		SortNo:      item.SortNo,
		Permissions: permissionCodes,
	})
}

func RolePostCreate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionRoleCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.CreateRoleRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	role, err := services.RoleService.CreateRole(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, &response.RoleResponse{
		ID:       role.ID,
		Name:     role.Name,
		Code:     role.Code,
		Status:   role.Status,
		IsSystem: role.IsSystem,
		SortNo:   role.SortNo,
	})
}

func RolePostUpdate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionRoleUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.UpdateRoleRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.RoleService.UpdateRole(req, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func RolePostDelete(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionRoleDelete); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.DeleteRoleRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.RoleService.DeleteRole(req.ID); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func RolePostUpdate_status(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionRoleUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.UpdateRoleStatusRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.RoleService.UpdateStatus(req.ID, req.Status, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func RolePostAssign_permission(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionRoleAssignPermission)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.AssignPermissionRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.RoleService.AssignPermissions(req.RoleID, req.PermissionIDs, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func RolePostUpdate_sort(ctx *gin.Context) {
	var ids []int64
	if err := params.ReadJSON(ctx, &ids); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.RoleService.UpdateSort(ids); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}
