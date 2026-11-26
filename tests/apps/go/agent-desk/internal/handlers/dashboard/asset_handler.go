package dashboard

import (
	"agent-desk/internal/builders"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/services"
	"strings"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func AssetAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAssetView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	cnd := params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "provider"},
		params.QueryFilter{ParamName: "status"},
		params.QueryFilter{ParamName: "createUserId"},
		params.QueryFilter{ParamName: "filename", Op: params.Like},
	).Desc("id")
	if strings.TrimSpace(ctx.Query("status")) == "" {
		cnd = cnd.Eq("status", enums.AssetStatusSuccess)
	}

	list, paging := services.AssetService.FindPageByCnd(cnd)
	results := make([]response.AssetResponse, 0, len(list))
	for _, item := range list {
		results = append(results, builders.BuildAsset(&item))
	}
	httpx.WriteJSON(ctx, &web.PageResult{Results: results, Page: paging})
}

func AssetGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAssetView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item := services.AssetService.Get(id)
	if item == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0214"))
		return
	}
	httpx.WriteJSON(ctx, builders.BuildAsset(item))
}

func AssetPostCreate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAssetCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.CreateAssetRequest{}
	if err := params.ReadForm(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	header, err := ctx.FormFile("file")
	if err != nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0323"))
		return
	}
	item, err := services.AssetService.UploadFile(header, req.Prefix, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildAsset(item))
}

func AssetPostDelete(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAssetDelete)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.DeleteAssetRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AssetService.DeleteAsset(req.ID, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}
