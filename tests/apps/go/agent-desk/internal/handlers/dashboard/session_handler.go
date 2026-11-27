package dashboard

import (
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/pkg/utils"
	"agent-desk/internal/services"
	"time"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func SessionAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionSessionView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	cnd := params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "userId"},
		params.QueryFilter{ParamName: "clientType"},
	).Desc("id")
	list, paging := services.LoginSessionService.FindPageByCnd(cnd)
	results := make([]response.SessionResponse, 0, len(list))
	for _, item := range list {
		username := ""
		if user := services.UserService.Get(item.UserID); user != nil {
			username = user.Username
		}
		results = append(results, response.SessionResponse{
			ID:         item.ID,
			UserID:     item.UserID,
			Username:   username,
			ClientType: item.ClientType,
			ClientIP:   item.ClientIP,
			UserAgent:  item.UserAgent,
			ExpiredAt:  item.ExpiredAt.Format(time.DateTime),
			RevokedAt:  utils.FormatTimePtr(item.RevokedAt),
			LastSeenAt: utils.FormatTimePtr(item.LastSeenAt),
		})
	}
	httpx.WriteJSON(ctx, &web.PageResult{Results: results, Page: paging})
}

func SessionPostRevoke(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionSessionRevoke)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.RevokeSessionRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.LoginSessionService.Revoke(req.ID, user.UserID, user.Username); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func SessionPostRevokeByUser(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionSessionRevoke)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.RevokeUserSessionsRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.LoginSessionService.RevokeByUser(req.UserID, user.UserID, user.Username); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}
