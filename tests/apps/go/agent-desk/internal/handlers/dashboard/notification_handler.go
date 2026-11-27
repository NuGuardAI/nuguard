package dashboard

import (
	"agent-desk/internal/pkg/httpx"
	"strings"

	"agent-desk/internal/builders"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"
	"agent-desk/internal/pkg/i18nx"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func NotificationAnyList(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionNotificationView)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	cnd := params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "type", ColumnName: "notification_type"},
	).Eq("recipient_user_id", operator.UserID).
		Eq("status", enums.StatusOk).
		Desc("id")

	switch strings.TrimSpace(ctx.Query("readStatus")) {
	case "unread":
		cnd.Where("read_at IS NULL")
	case "read":
		cnd.Where("read_at IS NOT NULL")
	}

	list, paging := services.NotificationService.FindPageByCnd(cnd)
	httpx.WriteJSON(ctx, &web.PageResult{
		Results: builders.BuildNotificationListWithLocale(list, i18nx.Locale(ctx)),
		Page:    paging,
	})
}

func NotificationGetUnread_count(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionNotificationView)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, &response.NotificationUnreadCountResponse{
		UnreadCount: services.NotificationService.CountUnread(operator.UserID),
	})
}

func NotificationPostMark_read(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionNotificationUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.MarkNotificationReadRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.NotificationService.MarkRead(req.ID, operator.UserID); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func NotificationPostMark_all_read(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionNotificationUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.NotificationService.MarkAllRead(operator.UserID); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}
