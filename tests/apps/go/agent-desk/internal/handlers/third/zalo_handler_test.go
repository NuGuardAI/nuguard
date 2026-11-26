package third

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"
	"agent-desk/internal/services"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/sqls"
)

func TestZaloPostWebhook_Handler(t *testing.T) {
	gin.SetMode(gin.TestMode)
	db := setupThirdHandlerTestDB(t)

	now := time.Now()
	agent := &models.AIAgent{
		Name:                "Zalo OA Bot Agent",
		ServiceMode:         enums.IMConversationServiceModeAIFirst,
		PublishedRevisionID: 1,
		WelcomeMessage:      "Xin chào từ Zalo OA!",
		Status:              enums.StatusOk,
		AuditFields:         models.AuditFields{CreatedAt: now, UpdatedAt: now},
	}
	_ = db.Create(agent)

	zaloConfig, _ := json.Marshal(dto.ZaloOAChannelConfig{
		AppID:         "1234567890",
		OAID:          "9876543210",
		AccessToken:   "zalo_oa_live_token_abc",
		WebhookSecret: "my_zalo_secret",
	})

	operator := &dto.AuthPrincipal{UserID: 1, Username: "admin"}
	channel, err := services.ChannelService.CreateChannel(request.CreateChannelRequest{
		Name:                  "Zalo OA Channel",
		ChannelType:           enums.ChannelTypeZaloOA,
		AIAgentID:             agent.ID,
		AIAgentRolloutPercent: 100,
		ConfigJSON:            string(zaloConfig),
		Status:                int(enums.StatusOk),
	}, operator)
	if err != nil {
		t.Fatalf("CreateChannel failed: %v", err)
	}

	router := gin.New()
	router.POST("/api/third/zalo/webhook/:channel_id", ZaloPostWebhook)
	router.POST("/api/third/zalo/webhook", ZaloPostWebhook)

	// Inbound message payload
	updatePayload := []byte(`{
		"event_name": "user_send_text",
		"app_id": "1234567890",
		"sender": {"id": "zalo_uid_999"},
		"recipient": {"id": "9876543210"},
		"message": {
			"msg_id": "zalo_msg_777",
			"text": "Chào bạn, tôi cần tư vấn mua license Crove Desk"
		},
		"info": {
			"display_name": "Nguyen Van C"
		},
		"timestamp": "1756201000"
	}`)

	req, _ := http.NewRequest(http.MethodPost, "/api/third/zalo/webhook/"+channel.ChannelID, bytes.NewBuffer(updatePayload))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Zalo-Signature", "my_zalo_secret")

	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 OK, got %d", rec.Code)
	}
	var resp map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &resp)
	if resp["error"] != float64(0) {
		t.Fatalf("expected error: 0, got: %+v", resp)
	}

	// Verify customer identity created
	identity := repositories.CustomerIdentityRepository.FindOne(db, sqls.NewCnd().
		Eq("external_source", enums.ExternalSourceZaloOA).
		Eq("external_id", "zalo_uid_999"))
	if identity == nil {
		t.Fatalf("expected customer identity for zalo_uid_999")
	}

	customer := repositories.CustomerRepository.Get(db, identity.CustomerID)
	if customer == nil || customer.Name != "Nguyen Van C" {
		t.Fatalf("unexpected customer: %+v", customer)
	}
}
