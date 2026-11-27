package services

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
	"gorm.io/gorm/schema"
)

func setupTelegramTestDB(t *testing.T) *gorm.DB {
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
	if err := db.AutoMigrate(
		&models.Channel{},
		&models.ChannelMessageOutbox{},
		&models.Customer{},
		&models.CustomerIdentity{},
		&models.CustomerContact{},
		&models.Conversation{},
		&models.ConversationParticipant{},
		&models.ConversationReadState{},
		&models.ConversationInterrupt{},
		&models.ConversationEventLog{},
		&models.Message{},
		&models.AIAgent{},
		&models.User{},
		&models.Role{},
		&models.UserRole{},
		&models.Permission{},
		&models.RolePermission{},
		&models.UserPermission{},
	); err != nil {
		t.Fatalf("migrate telegram test tables: %v", err)
	}
	sqls.SetDB(db)
	return db
}

func TestTelegramInboundAndOutboundFlow(t *testing.T) {
	db := setupTelegramTestDB(t)

	now := time.Now()
	// 1. Create AI Agent
	agent := &models.AIAgent{
		Name:                "Support Bot",
		ServiceMode:         enums.IMConversationServiceModeAIFirst,
		PublishedRevisionID: 1,
		WelcomeMessage:      "Welcome to Crove Desk!",
		Status:              enums.StatusOk,
		AuditFields: models.AuditFields{
			CreatedAt: now,
			UpdatedAt: now,
		},
	}
	_ = db.Create(agent)

	// 2. Create Telegram Channel
	tgConfig, _ := json.Marshal(dto.TelegramChannelConfig{
		BotToken:       "test-bot-token-123",
		BotUsername:    "crove_desk_bot",
		WebhookSecret:  "secret-webhook-token",
		WelcomeMessage: "Welcome to Telegram Support!",
	})

	operator := &dto.AuthPrincipal{UserID: 1, Username: "admin"}
	channel, err := ChannelService.CreateChannel(request.CreateChannelRequest{
		Name:                  "Telegram Support Channel",
		ChannelType:           enums.ChannelTypeTelegram,
		AIAgentID:             agent.ID,
		AIAgentRolloutPercent: 100,
		ConfigJSON:            string(tgConfig),
		Status:                int(enums.StatusOk),
	}, operator)
	if err != nil {
		t.Fatalf("CreateChannel failed: %v", err)
	}

	// 3. Simulate Inbound Telegram Webhook update
	updatePayload := []byte(`{
		"update_id": 998877,
		"message": {
			"message_id": 12345,
			"from": {
				"id": 888999,
				"is_bot": false,
				"first_name": "John",
				"last_name": "Doe",
				"username": "johndoe",
				"language_code": "vi"
			},
			"chat": {
				"id": 888999,
				"type": "private",
				"first_name": "John"
			},
			"date": 1756200000,
			"text": "Chào bạn, tôi cần hỗ trợ nâng cấp gói Crove Enterprise!"
		}
	}`)

	ctx := context.Background()
	err = TelegramInboundService.HandleWebhook(ctx, channel.ChannelID, "secret-webhook-token", updatePayload)
	if err != nil {
		t.Fatalf("HandleWebhook failed: %v", err)
	}

	// Verify Customer created
	identity := repositories.CustomerIdentityRepository.FindOne(db, sqls.NewCnd().
		Eq("external_source", enums.ExternalSourceTelegram).
		Eq("external_id", "888999"))
	if identity == nil {
		t.Fatalf("expected customer identity to be created for Telegram user")
	}

	customer := repositories.CustomerRepository.Get(db, identity.CustomerID)
	if customer == nil || customer.Name != "John Doe" {
		t.Fatalf("unexpected customer: %+v", customer)
	}

	// Verify Conversation created
	conv := repositories.ConversationRepository.FindOne(db, sqls.NewCnd().Eq("customer_id", customer.ID))
	if conv == nil || conv.ChannelID != channel.ID {
		t.Fatalf("unexpected conversation: %+v", conv)
	}

	// Verify Customer Message stored
	msg := repositories.MessageRepository.FindOne(db, sqls.NewCnd().
		Eq("conversation_id", conv.ID).
		Eq("sender_type", enums.IMSenderTypeCustomer))
	if msg == nil || msg.Content != "Chào bạn, tôi cần hỗ trợ nâng cấp gói Crove Enterprise!" {
		t.Fatalf("unexpected customer message: %+v", msg)
	}

	// 4. Simulate AI / Agent Reply and test Outbox Enqueue & Dispatch
	replyMsg, err := MessageService.SendAIMessage(conv.ID, agent.ID, "ai_msg_001", enums.IMMessageTypeText, "Cảm ơn bạn! Đội ngũ Crove sẽ hỗ trợ bạn ngay.", "", operator)
	if err != nil {
		t.Fatalf("SendAIMessage failed: %v", err)
	}

	outbox := ChannelMessageOutboxService.GetByMessageID(enums.ChannelTypeTelegram, replyMsg.ID)
	if outbox == nil {
		t.Fatalf("expected telegram outbox entry for AI message")
	}
	if outbox.SendStatus != string(enums.ChannelMessageOutboxStatusPending) && outbox.SendStatus != string(enums.ChannelMessageOutboxStatusSending) {
		t.Logf("Outbox send status: %s", outbox.SendStatus)
	}
}

func stringsContains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(substr) == 0 || (len(s) > 0 && len(substr) > 0 && indexOf(s, substr) >= 0))
}

func indexOf(s, substr string) int {
	for i := 0; i+len(substr) <= len(s); i++ {
		if s[i:i+len(substr)] == substr {
			return i
		}
	}
	return -1
}
