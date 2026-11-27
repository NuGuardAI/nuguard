package dashboard

import (
	"agent-desk/internal/builders"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func CompanyAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionCompanyView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	list, paging := services.CompanyService.FindPageByCnd(params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "status"},
		params.QueryFilter{ParamName: "name", Op: params.Like},
		params.QueryFilter{ParamName: "code", Op: params.Like},
	).Where("status <> ?", enums.StatusDeleted).Desc("id"))

	results := builders.BuildCompanyList(list)
	companyIDs := make([]int64, 0, len(results))
	for _, item := range results {
		companyIDs = append(companyIDs, item.ID)
	}
	countMap := services.CustomerService.CountByCompanyIDs(companyIDs)
	for i := range results {
		results[i].CustomerCount = countMap[results[i].ID]
	}
	httpx.WriteJSON(ctx, &web.PageResult{Results: results, Page: paging})
}

func CompanyGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionCompanyView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item := services.CompanyService.Get(id)
	if item == nil || item.Status == enums.StatusDeleted {
		httpx.WriteJSON(ctx, nil)
		return
	}
	ret := builders.BuildCompany(item)
	httpx.WriteJSON(ctx, &ret)
}

func CompanyPostCreate(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionCompanyCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.CreateCompanyRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.CompanyService.CreateCompany(req, user)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	ret := builders.BuildCompany(item)
	httpx.WriteJSON(ctx, &ret)
}

func CompanyPostUpdate(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionCompanyUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.UpdateCompanyRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.CompanyService.UpdateCompany(req, user); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func CompanyPostDelete(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionCompanyDelete)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.DeleteCompanyRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.CompanyService.DeleteCompany(req.ID, *user); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func CompanyPostUpdate_status(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionCompanyUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.UpdateCompanyStatusRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.CompanyService.UpdateStatus(req.ID, req.Status, user); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}
