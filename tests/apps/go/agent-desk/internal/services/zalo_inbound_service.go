package services

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/openidentity"
	"agent-desk/internal/zalo"
)

var ZaloOAInboundService = newZaloOAInboundService()

func newZaloOAInboundService() *zaloOAInboundService {
	return &zaloOAInboundService{}
}

type zaloOAInboundService struct{}

// HandleWebhook processes an incoming webhook Event from Zalo OA.
func (s *zaloOAInboundService) HandleWebhook(ctx context.Context, channelID string, signatureHeader string, rawPayload []byte) error {
	channelID = strings.TrimSpace(channelID)
	var channel *models.Channel
	if channelID != "" {
		channel = ChannelService.Take("channel_id = ? AND channel_type = ? AND status = ?", channelID, enums.ChannelTypeZaloOA, enums.StatusOk)
	}
	if channel == nil {
		channel = ChannelService.Take("channel_type = ? AND status = ?", enums.ChannelTypeZaloOA, enums.StatusOk)
	}
	if channel == nil {
		return errorsx.InvalidParam("zalo oa channel not found or disabled")
	}

	cfg, err := ChannelService.ParseZaloOAChannelConfig(channel.ConfigJSON)
	if err != nil || cfg == nil || cfg.AccessToken == "" {
		return errorsx.InvalidParam("zalo oa channel config invalid")
	}

	if cfg.WebhookSecret != "" && strings.TrimSpace(signatureHeader) != "" {
		// Optional header/secret verification
		if strings.TrimSpace(signatureHeader) != cfg.WebhookSecret {
			return errorsx.UnauthorizedI18n("error.auth.invalidSignature")
		}
	}

	var event zalo.WebhookEvent
	if err := json.Unmarshal(rawPayload, &event); err != nil {
		return fmt.Errorf("unmarshal zalo oa event failed: %w", err)
	}

	// Filter user-sent text messages
	if event.EventName != "user_send_text" && event.EventName != "user_send_image" && event.EventName != "user_send_file" {
		return nil
	}

	senderID := strings.TrimSpace(event.Sender.ID)
	if senderID == "" {
		return nil
	}

	text := ""
	if event.Message != nil {
		text = strings.TrimSpace(event.Message.Text)
	}
	if text == "" && event.EventName != "user_send_text" {
		text = fmt.Sprintf("[%s]", event.EventName)
	}
	if text == "" {
		return nil
	}

	// 1. Resolve customer identity
	name := fmt.Sprintf("Zalo User %s", senderID)
	if displayName, ok := event.Info["display_name"].(string); ok && strings.TrimSpace(displayName) != "" {
		name = strings.TrimSpace(displayName)
	}

	externalUser := openidentity.ExternalUser{
		ExternalSource: enums.ExternalSourceZaloOA,
		ExternalID:     senderID,
		ExternalName:   name,
	}

	// 2. Create or match Conversation
	conversation, err := ConversationService.Create(externalUser, channel.ID, channel.AIAgentID)
	if err != nil {
		return fmt.Errorf("create zalo conversation failed: %w", err)
	}

	// 3. Send message through MessageService
	msgID := ""
	if event.Message != nil {
		msgID = event.Message.MsgID
	}
	if msgID == "" {
		msgID = fmt.Sprintf("zalo_%s_%s", senderID, event.Timestamp)
	}
	clientMsgID := fmt.Sprintf("zalo_%s", msgID)

	payloadMap := map[string]any{
		"zalo_user_id":   senderID,
		"zalo_msg_id":    msgID,
		"zalo_app_id":    event.AppID,
		"zalo_recipient": event.Recipient.ID,
		"zalo_event":     event.EventName,
	}
	payloadBytes, _ := json.Marshal(payloadMap)

	_, err = MessageService.SendCustomerMessage(
		conversation.ID,
		clientMsgID,
		enums.IMMessageTypeText,
		text,
		string(payloadBytes),
		externalUser,
	)
	if err != nil {
		return fmt.Errorf("send customer message failed: %w", err)
	}

	return nil
}
