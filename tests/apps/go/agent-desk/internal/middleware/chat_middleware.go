package middleware

import (
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/services"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func ExternalUserMiddleware(ctx *gin.Context) {
	channel := services.ChannelService.GetEnabledChannel(ctx)
	if channel == nil {
		ctx.JSON(200, httpx.JsonErrorMsg(ctx, "error.e0210"))
		ctx.Abort()
		return
	}
	result, err := services.CustomerSessionService.VerifyRequest(ctx, channel)
	if err != nil {
		ctx.JSON(200, web.JsonError(err))
		ctx.Abort()
		return
	}
	services.CustomerSessionService.SetRefreshHeaders(ctx, result)
	httpx.SetExternalUser(ctx, result.ExternalUser)
	ctx.Next()
}
