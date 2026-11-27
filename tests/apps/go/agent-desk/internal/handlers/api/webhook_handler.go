package api

import (
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/pkg/httpx/params"
	"agent-desk/internal/services"
	"bytes"
	"io"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
)

func OrgSyncWebhook(ctx *gin.Context) {
	bodyBytes, err := io.ReadAll(ctx.Request.Body)
	if err != nil {
		ctx.JSON(http.StatusBadRequest, gin.H{"error": "failed to read body"})
		return
	}
	ctx.Request.Body = io.NopCloser(bytes.NewBuffer(bodyBytes))

	signature := firstHeader(ctx, "X-Webhook-Signature", "X-Org-Signature", "X-DOS-Signature", "X-Hub-Signature-256")
	if !services.WebhookSyncService.VerifySignature(bodyBytes, signature) {
		ctx.JSON(http.StatusUnauthorized, gin.H{"error": "invalid signature"})
		return
	}

	req := request.OrgSyncWebhookRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	if err := services.WebhookSyncService.HandleOrgSync(req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	httpx.WriteJSON(ctx, gin.H{"success": true})
}

func DOSOrgSyncWebhook(ctx *gin.Context) {
	OrgSyncWebhook(ctx)
}

func firstHeader(ctx *gin.Context, keys ...string) string {
	for _, k := range keys {
		if val := strings.TrimSpace(ctx.GetHeader(k)); val != "" {
			return val
		}
	}
	return ""
}
