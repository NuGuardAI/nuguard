package dashboard

import (
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"
	"agent-desk/internal/pkg/i18nx"

	"github.com/gin-gonic/gin"
)

func DashboardGetOverview(ctx *gin.Context) {
	rangeValue, _ := params.Get(ctx, "range")
	httpx.WriteJSON(ctx, services.DashboardService.GetOverview(rangeValue, i18nx.Locale(ctx)))
}
