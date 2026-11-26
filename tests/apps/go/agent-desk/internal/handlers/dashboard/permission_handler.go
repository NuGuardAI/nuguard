package dashboard

import (
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/common/strs"
	"github.com/mlogclub/simple/web"
)

func PermissionAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionPermissionView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	cnd := params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "groupName"},
		params.QueryFilter{ParamName: "type"},
		params.QueryFilter{ParamName: "status"},
	).Desc("id")

	if keyword, _ := params.Get(ctx, "keyword"); strs.IsNotBlank(keyword) {
		cnd.Where("(name LIKE ? OR code LIKE ?)", "%"+keyword+"%", "%"+keyword+"%")
	}

	list, paging := services.PermissionService.FindPageByCnd(cnd)
	results := make([]response.PermissionResponse, 0, len(list))
	for _, item := range list {
		results = append(results, response.PermissionResponse{
			ID:        item.ID,
			Name:      item.Name,
			Code:      item.Code,
			Type:      item.Type,
			GroupName: item.GroupName,
			Method:    item.Method,
			ApiPath:   item.APIPath,
			Status:    item.Status,
			SortNo:    item.SortNo,
		})
	}
	httpx.WriteJSON(ctx, &web.PageResult{Results: results, Page: paging})
}

func PermissionGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionPermissionView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	item := services.PermissionService.Get(id)
	if item == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0236"))
		return
	}
	httpx.WriteJSON(ctx, &response.PermissionResponse{
		ID:        item.ID,
		Name:      item.Name,
		Code:      item.Code,
		Type:      item.Type,
		GroupName: item.GroupName,
		Method:    item.Method,
		ApiPath:   item.APIPath,
		Status:    item.Status,
		SortNo:    item.SortNo,
	})
}

func PermissionPostSync(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionPermissionSync); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	result, err := services.PermissionService.SyncBuiltinPermissions()
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, result)
}
