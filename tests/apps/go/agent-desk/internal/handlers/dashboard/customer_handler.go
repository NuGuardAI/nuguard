package dashboard

import (
	"agent-desk/internal/builders"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func CustomerPostList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionCustomerView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	var req request.CustomerListRequest
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	list, paging := services.CustomerService.ListCustomers(req)
	httpx.WriteJSON(ctx, &web.PageResult{Results: builders.BuildCustomerList(list), Page: paging})
}

func CustomerGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionCustomerView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item := services.CustomerService.Get(id)
	if item == nil || item.Status == enums.StatusDeleted {
		httpx.WriteJSON(ctx, nil)
		return
	}
	ret := builders.BuildCustomer(item)
	httpx.WriteJSON(ctx, &ret)
}

// PostSave_profile POST /save_profile — 客户主信息与联系方式在同一事务中保存。
func CustomerPostSave_profile(ctx *gin.Context) {
	req := request.SaveCustomerProfileRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	createMode := req.ID == nil || *req.ID <= 0
	var user *dto.AuthPrincipal
	var err error
	if createMode {
		user, err = services.AuthService.RequirePermission(ctx, constants.PermissionCustomerCreate)
	} else {
		user, err = services.AuthService.RequirePermission(ctx, constants.PermissionCustomerUpdate)
	}
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.CustomerService.SaveCustomerProfile(req, user)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	ret := builders.BuildCustomer(item)
	httpx.WriteJSON(ctx, &ret)
}

func CustomerPostCreate(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionCustomerCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.CreateCustomerRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.CustomerService.CreateCustomer(req, user)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	ret := builders.BuildCustomer(item)
	httpx.WriteJSON(ctx, &ret)
}

func CustomerPostUpdate(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionCustomerUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.UpdateCustomerRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.CustomerService.UpdateCustomer(req, user); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func CustomerPostDelete(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionCustomerDelete)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.DeleteCustomerRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.CustomerService.DeleteCustomer(req.ID, *user); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func CustomerPostUpdate_status(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionCustomerUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.UpdateCustomerStatusRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.CustomerService.UpdateStatus(req.ID, req.Status, user); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}
