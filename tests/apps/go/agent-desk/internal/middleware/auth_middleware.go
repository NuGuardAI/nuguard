package middleware

import (
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/i18nx"
	"agent-desk/internal/services"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func AuthMiddleware(ctx *gin.Context) {
	if !authenticateRequest(ctx) {
		return
	}
	ctx.Next()
}

func authenticateRequest(ctx *gin.Context) bool {
	principal, err := services.AuthService.Authenticate(ctx)
	if err != nil {
		result := web.JsonError(err)
		result.Message = i18nx.T(ctx, "error.auth.expired")
		ctx.JSON(200, result)
		ctx.Abort()
		return false
	}
	if principal.UserType != enums.UserTypeEmployee {
		ctx.JSON(200, web.JsonErrorCode(3001, i18nx.T(ctx, "error.e0225")))
		ctx.Abort()
		return false
	}
	return true
}
