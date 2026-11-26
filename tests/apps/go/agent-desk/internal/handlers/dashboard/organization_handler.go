package dashboard

import (
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/pkg/httpx/params"
	"agent-desk/internal/services"

	"github.com/gin-gonic/gin"
)

func OrganizationUserList(ctx *gin.Context) {
	principal := services.AuthService.GetAuthPrincipal(ctx)
	if principal == nil {
		httpx.WriteJSON(ctx, nil)
		return
	}

	ret, err := services.OrganizationService.GetUserOrganizations(principal.UserID)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, ret)
}

func OrganizationSwitch(ctx *gin.Context) {
	principal := services.AuthService.GetAuthPrincipal(ctx)
	if principal == nil {
		httpx.WriteJSON(ctx, nil)
		return
	}

	req := request.OrganizationSwitchRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	org, err := services.OrganizationService.SwitchActiveOrganization(principal.UserID, req.OrganizationID)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	httpx.WriteJSON(ctx, org)
}
