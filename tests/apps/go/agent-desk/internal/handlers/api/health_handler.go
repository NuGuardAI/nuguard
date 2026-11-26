package api

import (
	"agent-desk/internal/pkg/httpx"

	"github.com/gin-gonic/gin"
)

type healthResponse struct {
	Status string `json:"status"`
}

func Health(ctx *gin.Context) {
	httpx.WriteJSON(ctx, &healthResponse{Status: "ok"})
}
