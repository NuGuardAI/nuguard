package third

import (
	"bytes"
	"io"
	"net/http"
	"strings"

	"agent-desk/internal/services"

	"github.com/gin-gonic/gin"
)

// ZaloPostWebhook receives incoming Webhook events from Zalo Official Account.
func ZaloPostWebhook(ctx *gin.Context) {
	channelID := strings.TrimSpace(ctx.Param("channel_id"))
	if channelID == "" {
		channelID = strings.TrimSpace(ctx.Query("channel_id"))
	}

	sigHeader := ctx.GetHeader("X-Zalo-Signature")
	if sigHeader == "" {
		sigHeader = ctx.GetHeader("X-Hub-Signature")
	}

	bodyBytes, err := io.ReadAll(ctx.Request.Body)
	if err != nil {
		ctx.JSON(http.StatusBadRequest, gin.H{"error": 1, "message": "failed to read body"})
		return
	}
	ctx.Request.Body = io.NopCloser(bytes.NewBuffer(bodyBytes))

	if err := services.ZaloOAInboundService.HandleWebhook(ctx.Request.Context(), channelID, sigHeader, bodyBytes); err != nil {
		ctx.JSON(http.StatusOK, gin.H{"error": 1, "message": err.Error()})
		return
	}

	ctx.JSON(http.StatusOK, gin.H{"error": 0, "message": "Success"})
}
