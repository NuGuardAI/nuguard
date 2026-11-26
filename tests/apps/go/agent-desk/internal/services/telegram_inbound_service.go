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
	"agent-desk/internal/telegram"
)

var TelegramInboundService = newTelegramInboundService()

func newTelegramInboundService() *telegramInboundService {
	return &telegramInboundService{}
}

type telegramInboundService struct{}

// HandleWebhook processes an incoming webhook Update from Telegram.
func (s *telegramInboundService) HandleWebhook(ctx context.Context, channelID string, secretHeader string, rawPayload []byte) error {
	channelID = strings.TrimSpace(channelID)
	var channel *models.Channel
	if channelID != "" {
		channel = ChannelService.Take("channel_id = ? AND channel_type = ? AND status = ?", channelID, enums.ChannelTypeTelegram, enums.StatusOk)
	}
	if channel == nil {
		channel = ChannelService.Take("channel_type = ? AND status = ?", enums.ChannelTypeTelegram, enums.StatusOk)
	}
	if channel == nil {
		return errorsx.InvalidParam("telegram channel not found or disabled")
	}

	cfg, err := ChannelService.ParseTelegramChannelConfig(channel.ConfigJSON)
	if err != nil || cfg == nil || cfg.BotToken == "" {
		return errorsx.InvalidParam("telegram channel config invalid")
	}

	if cfg.WebhookSecret != "" && strings.TrimSpace(secretHeader) != cfg.WebhookSecret {
		return errorsx.UnauthorizedI18n("error.auth.invalidSignature")
	}

	var update telegram.Update
	if err := json.Unmarshal(rawPayload, &update); err != nil {
		return fmt.Errorf("unmarshal telegram update failed: %w", err)
	}

	if update.Message == nil {
		return nil // Ignore non-message updates (e.g. edits, inline queries)
	}

	msg := update.Message
	if msg.From == nil || msg.Chat.ID == 0 {
		return nil
	}

	text := strings.TrimSpace(msg.Text)
	if text == "" {
		text = strings.TrimSpace(msg.Caption)
	}
	if text == "" {
		return nil // Ignore media without captions for now
	}

	// 1. Resolve customer identity
	externalID := fmt.Sprintf("%d", msg.Chat.ID)
	name := strings.TrimSpace(msg.From.FirstName + " " + msg.From.LastName)
	if name == "" {
		name = strings.TrimSpace(msg.From.Username)
	}
	if name == "" {
		name = fmt.Sprintf("Telegram User %d", msg.From.ID)
	}

	externalUser := openidentity.ExternalUser{
		ExternalSource: enums.ExternalSourceTelegram,
		ExternalID:     externalID,
		ExternalName:   name,
	}

	// 2. Create or match Conversation
	conversation, err := ConversationService.Create(externalUser, channel.ID, channel.AIAgentID)
	if err != nil {
		return fmt.Errorf("create telegram conversation failed: %w", err)
	}

	// 3. Send message through MessageService (automatically triggers AI response loop or agent notification)
	clientMsgID := fmt.Sprintf("tg_%d_%d", update.UpdateID, msg.MessageID)
	payloadMap := map[string]any{
		"telegram_message_id": msg.MessageID,
		"telegram_chat_id":    msg.Chat.ID,
		"telegram_update_id":  update.UpdateID,
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
