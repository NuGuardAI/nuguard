package dashboard

import (
	"agent-desk/internal/builders"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
)

// AnyList GET/POST /customer-contact/list?customerId=
func CustomerContactAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionCustomerView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	customerID, _ := params.GetInt64(ctx, "customerId")
	if customerID <= 0 {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0065"))
		return
	}
	list := services.CustomerContactService.FindActiveByCustomerID(customerID)
	httpx.WriteJSON(ctx, builders.BuildCustomerContactList(list))
}

func CustomerContactPostCreate(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionCustomerUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.CreateCustomerContactRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.CustomerContactService.CreateCustomerContact(req, user)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	ret := builders.BuildCustomerContactResponse(item)
	httpx.WriteJSON(ctx, &ret)
}

func CustomerContactPostUpdate(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionCustomerUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.UpdateCustomerContactRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.CustomerContactService.UpdateCustomerContact(req, user); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func CustomerContactPostDelete(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionCustomerUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.DeleteCustomerContactRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.CustomerContactService.DeleteCustomerContact(req.ID, user); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}
