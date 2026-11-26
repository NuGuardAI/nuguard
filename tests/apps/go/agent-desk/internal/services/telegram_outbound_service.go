package services

import (
	"context"
	"log/slog"
	"strconv"
	"time"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"
	"agent-desk/internal/telegram"

	"github.com/mlogclub/simple/sqls"
)

const (
	telegramOutboxBatchSize = 20
	telegramOutboxMaxRetry  = 5
)

var TelegramOutboundService = newTelegramOutboundService()

func newTelegramOutboundService() *telegramOutboundService {
	return &telegramOutboundService{}
}

type telegramOutboundService struct{}

func (s *telegramOutboundService) DispatchPendingOutbox() int {
	return s.doDispatchPendingOutbox(telegramOutboxBatchSize)
}

func (s *telegramOutboundService) doDispatchPendingOutbox(limit int) int {
	if limit <= 0 {
		limit = telegramOutboxBatchSize
	}
	items := ChannelMessageOutboxService.ListPending(enums.ChannelTypeTelegram, limit)
	if len(items) == 0 {
		return 0
	}

	successCount := 0
	for i := range items {
		if err := s.processOutbox(items[i].ID); err != nil {
			slog.Warn("process telegram outbox failed",
				"outbox_id", items[i].ID,
				"error", err,
			)
			continue
		}
		successCount++
	}
	return successCount
}

func (s *telegramOutboundService) processOutbox(outboxID int64) error {
	outbox := ChannelMessageOutboxService.Get(outboxID)
	if outbox == nil {
		return nil
	}
	if outbox.ChannelType != enums.ChannelTypeTelegram {
		return nil
	}
	if outbox.SendStatus == string(enums.ChannelMessageOutboxStatusSent) {
		return nil
	}
	if outbox.NextRetryAt != nil && outbox.NextRetryAt.After(time.Now()) {
		return nil
	}

	if err := ChannelMessageOutboxService.Updates(outbox.ID, map[string]any{
		"send_status": string(enums.ChannelMessageOutboxStatusSending),
		"updated_at":  time.Now(),
	}); err != nil {
		return err
	}

	message := MessageService.Get(outbox.MessageID)
	if message == nil {
		return s.markOutboxFailed(outbox, "message not found")
	}
	conversation := ConversationService.Get(outbox.ConversationID)
	if conversation == nil {
		return s.markOutboxFailed(outbox, "conversation not found")
	}

	channel := ChannelService.Get(conversation.ChannelID)
	if channel == nil || channel.Status != enums.StatusOk {
		return s.markOutboxFailed(outbox, "telegram channel not found or disabled")
	}
	cfg, err := ChannelService.ParseTelegramChannelConfig(channel.ConfigJSON)
	if err != nil || cfg == nil || cfg.BotToken == "" {
		return s.markOutboxFailed(outbox, "telegram bot token not configured")
	}

	// Resolve target Telegram ChatID
	var chatID int64
	customerIdentity := repositories.CustomerIdentityRepository.FindOne(sqls.DB(), sqls.NewCnd().
		Eq("customer_id", conversation.CustomerID).
		Eq("external_source", enums.ExternalSourceTelegram))
	if customerIdentity != nil {
		if id, err := strconv.ParseInt(customerIdentity.ExternalID, 10, 64); err == nil {
			chatID = id
		}
	}
	if chatID == 0 {
		return s.markOutboxFailed(outbox, "unable to resolve telegram chat_id")
	}

	// Send message via Telegram Client
	client := telegram.NewClient(cfg.BotToken)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	_, sendErr := client.SendMessage(ctx, telegram.SendMessageRequest{
		ChatID: chatID,
		Text:   message.Content,
	})

	if sendErr != nil {
		return s.markOutboxFailed(outbox, sendErr.Error())
	}

	return ChannelMessageOutboxService.Updates(outbox.ID, map[string]any{
		"send_status": string(enums.ChannelMessageOutboxStatusSent),
		"sent_at":     time.Now(),
		"updated_at":  time.Now(),
	})
}

func (s *telegramOutboundService) markOutboxFailed(outbox *models.ChannelMessageOutbox, errMsg string) error {
	if outbox == nil {
		return nil
	}
	retryCount := outbox.RetryCount + 1
	status := string(enums.ChannelMessageOutboxStatusFailed)
	if retryCount >= telegramOutboxMaxRetry {
		status = string(enums.ChannelMessageOutboxStatusIgnored)
	}
	nextRetryAt := time.Now().Add(time.Duration(retryCount*30) * time.Second)

	return ChannelMessageOutboxService.Updates(outbox.ID, map[string]any{
		"send_status":   status,
		"retry_count":   retryCount,
		"next_retry_at": &nextRetryAt,
		"last_error":    errMsg,
		"updated_at":    time.Now(),
	})
}
