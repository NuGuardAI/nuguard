package httpx

import (
	"agent-desk/internal/pkg/i18nx"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func JsonErrorMsg(ctx *gin.Context, key string, args ...any) *web.JsonResult {
	return web.JsonErrorMsg(i18nx.T(ctx, key, args...))
}
