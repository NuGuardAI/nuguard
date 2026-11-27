package httpx

import (
	"agent-desk/internal/pkg/i18nx"
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func GetPathInt64(ctx *gin.Context, name string) (int64, bool) {
	value, err := strconv.ParseInt(ctx.Param(name), 10, 64)
	if err != nil {
		WriteHttpStatusJSON(ctx, http.StatusBadRequest, web.JsonErrorMsg(i18nx.T(ctx, "error.path.invalid")))
		return 0, false
	}
	return value, true
}
