package services

import (
	"context"
	"encoding/json"
	"strconv"
	"testing"
	"time"

	"agent-desk/internal/lark"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

func setupLarkTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	return setupSlackTestDB(t)
}

// seedLarkChannel creates the channel row plus the AI agent its rollout
// config requires, mirroring how a real channel is provisioned.
func seedLarkChannel(t *testing.T, db *gorm.DB, verificationToken string) *models.Channel {
	t.Helper()
	now := time.Now()
	aiAgent := &models.AIAgent{
		Name:                "Support AI",
		Status:              enums.StatusOk,
		PublishedRevisionID: 1,
		AuditFields:         models.AuditFields{CreatedAt: now, UpdatedAt: now},
	}
	if err := db.Create(aiAgent).Error; err != nil {
		t.Fatalf("create ai agent: %v", err)
	}
	cfgBytes, err := json.Marshal(dto.LarkChannelConfig{
		AppID:             "cli_app",
		AppSecret:         "lark-secret",
		VerificationToken: verificationToken,
		Domain:            lark.LarkChannelDomainLark,
	})
	if err != nil {
		t.Fatalf("marshal lark config: %v", err)
	}
	channel := &models.Channel{
		ChannelType:           enums.ChannelTypeLark,
		ChannelID:             "lark_test_channel",
		AIAgentID:             aiAgent.ID,
		AIAgentRolloutPercent: 100,
		Name:                  "Lark Support",
		ConfigJSON:            string(cfgBytes),
		Status:                enums.StatusOk,
		AuditFields:           models.AuditFields{CreatedAt: now, UpdatedAt: now},
	}
	if err := db.Create(channel).Error; err != nil {
		t.Fatalf("create lark channel: %v", err)
	}
	return channel
}

func larkEventPayload(eventID, token, senderType, openID, messageID, message_type, content string) []byte {
	return larkEventPayloadRaw(eventID, token, senderType, openID, messageID, message_type, strconv.Quote(content))
}

// larkEventPayloadRaw takes the content already encoded as a JSON string value.
func larkEventPayloadRaw(eventID, token, senderType, openID, messageID, message_type, content string) []byte {
	return []byte(`{
		"schema": "2.0",
		"header": {
			"event_id": "` + eventID + `",
			"event_type": "im.message.receive_v1",
			"token": "` + token + `",
			"app_id": "cli_app"
		},
		"event": {
			"sender": {"sender_type": "` + senderType + `", "sender_id": {"open_id": "` + openID + `"}},
			"message": {
				"message_id": "` + messageID + `",
				"chat_id": "oc_chat",
				"chat_type": "p2p",
				"message_type": "` + message_type + `",
				"content": ` + content + `
			}
		}
	}`)
}

func countLarkMessages(t *testing.T, db *gorm.DB, messageID string) int64 {
	t.Helper()
	var count int64
	if err := db.Table("t_message").Where("client_msg_id = ?", "lark_"+messageID).Count(&count).Error; err != nil {
		t.Fatalf("count messages: %v", err)
	}
	return count
}

func TestLarkInboundAnswersURLVerification(t *testing.T) {
	payload := []byte(`{"challenge":"abc123","token":"tok","type":"url_verification"}`)
	challenge, err := LarkInboundService.HandleWebhook(context.Background(), "", payload)
	if err != nil {
		t.Fatalf("HandleWebhook: %v", err)
	}
	if challenge == nil || *challenge != "abc123" {
		t.Fatalf("challenge = %v, want abc123", challenge)
	}
}

func TestLarkInboundRejectsWrongVerificationToken(t *testing.T) {
	db := setupLarkTestDB(t)
	channel := seedLarkChannel(t, db, "expected-token")

	payload := larkEventPayload("evt1", "wrong", "user", "ou_1", "om_1", "text", `{"text":"hi"}`)
	if _, err := LarkInboundService.HandleWebhook(context.Background(), channel.ChannelID, payload); err == nil {
		t.Fatal("wrong verification token must be rejected")
	}
	if countLarkMessages(t, db, "om_1") != 0 {
		t.Fatal("a rejected delivery stored a message")
	}
}

func TestLarkInboundAcceptsVerifiedDelivery(t *testing.T) {
	db := setupLarkTestDB(t)
	channel := seedLarkChannel(t, db, "expected-token")

	payload := larkEventPayload("evt2", "expected-token", "user", "ou_777", "om_777", "text", `{"text":"xin chao"}`)
	if _, err := LarkInboundService.HandleWebhook(context.Background(), channel.ChannelID, payload); err != nil {
		t.Fatalf("HandleWebhook: %v", err)
	}

	identity := repositories.CustomerIdentityRepository.FindOne(sqls.DB(), sqls.NewCnd().
		Eq("external_source", enums.ExternalSourceLark).
		Eq("external_id", "ou_777"))
	if identity == nil {
		t.Fatal("expected customer identity for ou_777")
	}

	msg := repositories.MessageRepository.FindOne(sqls.DB(), sqls.NewCnd().
		Eq("client_msg_id", "lark_om_777"))
	if msg == nil {
		t.Fatal("expected the customer message to be stored")
	}
	if msg.Content != "xin chao" {
		t.Fatalf("content = %q", msg.Content)
	}
}

// Deliveries that must never provision anything - app/bot senders and
// non-text messages - are ignored silently.
func TestLarkInboundIgnoresAppSenderAndNonText(t *testing.T) {
	db := setupLarkTestDB(t)
	channel := seedLarkChannel(t, db, "expected-token")

	appSender := larkEventPayload("evt3", "expected-token", "app", "ou_app", "om_3", "text", `{"text":"bot echo"}`)
	if _, err := LarkInboundService.HandleWebhook(context.Background(), channel.ChannelID, appSender); err != nil {
		t.Fatalf("app-sender delivery should be ignored silently: %v", err)
	}

	nonText := larkEventPayloadRaw("evt4", "expected-token", "user", "ou_1", "om_4", "image", `"{\"image_key\":\"img_1\"}"`)
	if _, err := LarkInboundService.HandleWebhook(context.Background(), channel.ChannelID, nonText); err != nil {
		t.Fatalf("non-text delivery should be ignored silently: %v", err)
	}

	if countLarkMessages(t, db, "om_3") != 0 || countLarkMessages(t, db, "om_4") != 0 {
		t.Fatal("ignored deliveries must not store messages")
	}
}

// A delivery on the bare webhook path (no :channel_id) resolves its channel
// through the app id the event was issued for.
func TestLarkInboundResolvesChannelByAppID(t *testing.T) {
	db := setupLarkTestDB(t)
	seedLarkChannel(t, db, "expected-token")

	payload := larkEventPayload("evt5", "expected-token", "user", "ou_5", "om_5", "text", `{"text":"via app id"}`)
	if _, err := LarkInboundService.HandleWebhook(context.Background(), "", payload); err != nil {
		t.Fatalf("HandleWebhook: %v", err)
	}
	if countLarkMessages(t, db, "om_5") != 1 {
		t.Fatal("expected identity provisioning through the app-id fallback")
	}
}
