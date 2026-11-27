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
	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
	"gorm.io/gorm/schema"
)

func setupThirdHandlerTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{
		NamingStrategy: schema.NamingStrategy{
			TablePrefix:   "t_",
			SingularTable: true,
		},
	})
	if err != nil {
		t.Fatalf("open sqlite db: %v", err)
	}
	_ = db.AutoMigrate(
		&models.Channel{},
		&models.ChannelMessageOutbox{},
		&models.Customer{},
		&models.CustomerIdentity{},
		&models.CustomerContact{},
		&models.Conversation{},
		&models.ConversationParticipant{},
		&models.ConversationReadState{},
		&models.ConversationEventLog{},
		&models.ConversationInterrupt{},
		&models.Message{},
		&models.AIAgent{},
		&models.User{},
		&models.Role{},
		&models.UserRole{},
		&models.Permission{},
		&models.RolePermission{},
		&models.UserPermission{},
	)
	sqls.SetDB(db)
	return db
}

func TestTelegramPostWebhook_Handler(t *testing.T) {
	gin.SetMode(gin.TestMode)
	db := setupThirdHandlerTestDB(t)

	now := time.Now()
	agent := &models.AIAgent{
		Name:                "Telegram Bot Agent",
		ServiceMode:         enums.IMConversationServiceModeAIFirst,
		PublishedRevisionID: 1,
		WelcomeMessage:      "Hello!",
		Status:              enums.StatusOk,
		AuditFields:         models.AuditFields{CreatedAt: now, UpdatedAt: now},
	}
	_ = db.Create(agent)

	tgConfig, _ := json.Marshal(dto.TelegramChannelConfig{
		BotToken:       "123456:ABC-DEF",
		BotUsername:    "test_bot",
		WebhookSecret:  "my_secret_token_123",
		WelcomeMessage: "Welcome!",
	})

	operator := &dto.AuthPrincipal{UserID: 1, Username: "admin"}
	channel, err := services.ChannelService.CreateChannel(request.CreateChannelRequest{
		Name:                  "Telegram Channel",
		ChannelType:           enums.ChannelTypeTelegram,
		AIAgentID:             agent.ID,
		AIAgentRolloutPercent: 100,
		ConfigJSON:            string(tgConfig),
		Status:                int(enums.StatusOk),
	}, operator)
	if err != nil {
		t.Fatalf("CreateChannel failed: %v", err)
	}

	router := gin.New()
	router.POST("/api/third/telegram/webhook/:channel_id", TelegramPostWebhook)
	router.POST("/api/third/telegram/webhook", TelegramPostWebhook)

	// 1. Test unauthorized when secret doesn't match
	updatePayload := []byte(`{
		"update_id": 112233,
		"message": {
			"message_id": 999,
			"from": {"id": 777888, "first_name": "Alice"},
			"chat": {"id": 777888, "type": "private"},
			"text": "Hello from Telegram!"
		}
	}`)

	req, _ := http.NewRequest(http.MethodPost, "/api/third/telegram/webhook/"+channel.ChannelID, bytes.NewBuffer(updatePayload))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Telegram-Bot-Api-Secret-Token", "wrong_token")

	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 OK wrapper, got: %d", rec.Code)
	}
	var resp map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &resp)
	if resp["ok"] == true {
		t.Fatalf("expected error for invalid secret token")
	}

	// 2. Test success with valid secret
	req2, _ := http.NewRequest(http.MethodPost, "/api/third/telegram/webhook/"+channel.ChannelID, bytes.NewBuffer(updatePayload))
	req2.Header.Set("Content-Type", "application/json")
	req2.Header.Set("X-Telegram-Bot-Api-Secret-Token", "my_secret_token_123")

	rec2 := httptest.NewRecorder()
	router.ServeHTTP(rec2, req2)

	if rec2.Code != http.StatusOK {
		t.Fatalf("expected 200 OK, got %d", rec2.Code)
	}
	var resp2 map[string]any
	_ = json.Unmarshal(rec2.Body.Bytes(), &resp2)
	if resp2["ok"] != true {
		t.Fatalf("expected ok: true, got: %+v", resp2)
	}

	// Verify message in database
	identity := repositories.CustomerIdentityRepository.FindOne(db, sqls.NewCnd().
		Eq("external_source", enums.ExternalSourceTelegram).
		Eq("external_id", "777888"))
	if identity == nil {
		t.Fatalf("expected customer identity for 777888")
	}
}
