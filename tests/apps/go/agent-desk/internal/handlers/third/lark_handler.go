package third

import (
	"bytes"
	"io"
	"net/http"
	"strings"

	"agent-desk/internal/services"

	"github.com/gin-gonic/gin"
)

// LarkPostWebhook receives incoming Lark/Feishu event-callback payloads. The
// channel-scoped path lets one deployment serve several Lark apps; the bare
// path resolves the channel through the event's own app id.
func LarkPostWebhook(ctx *gin.Context) {
	channelID := strings.TrimSpace(ctx.Param("channel_id"))
	if channelID == "" {
		channelID = strings.TrimSpace(ctx.Query("channel_id"))
	}

	bodyBytes, err := io.ReadAll(ctx.Request.Body)
	if err != nil {
		ctx.JSON(http.StatusBadRequest, gin.H{"ok": false, "error": "failed to read body"})
		return
	}
	ctx.Request.Body = io.NopCloser(bytes.NewBuffer(bodyBytes))

	challenge, err := services.LarkInboundService.HandleWebhook(ctx.Request.Context(), channelID, bodyBytes)
	if err != nil {
		// Lark retries events that answer non-200, so application-level
		// failures answer 200 ok=false and the job queue owns retries.
		ctx.JSON(http.StatusOK, gin.H{"ok": false, "error": err.Error()})
		return
	}

	if challenge != nil {
		ctx.JSON(http.StatusOK, gin.H{"challenge": *challenge})
		return
	}

	ctx.JSON(http.StatusOK, gin.H{"ok": true})
}
