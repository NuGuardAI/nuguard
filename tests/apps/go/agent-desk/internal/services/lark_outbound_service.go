package services

import (
	"context"
	"encoding/json"
	"log/slog"
	"time"

	"agent-desk/internal/lark"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"
	"agent-desk/internal/services/storage"

	"github.com/mlogclub/simple/sqls"
)

const (
	larkOutboxBatchSize = 20
	larkOutboxMaxRetry  = 5
)

var LarkOutboundService = newLarkOutboundService()

func newLarkOutboundService() *larkOutboundService {
	return &larkOutboundService{}
}

type larkOutboundService struct{}

func (s *larkOutboundService) DispatchPendingOutbox() int {
	return s.doDispatchPendingOutbox(larkOutboxBatchSize)
}

func (s *larkOutboundService) doDispatchPendingOutbox(limit int) int {
	if limit <= 0 {
		limit = larkOutboxBatchSize
	}
	items := ChannelMessageOutboxService.ListPending(enums.ChannelTypeLark, limit)
	if len(items) == 0 {
		return 0
	}

	successCount := 0
	for i := range items {
		if err := s.processOutbox(items[i].ID); err != nil {
			slog.Warn("process lark outbox failed",
				"outbox_id", items[i].ID,
				"error", err,
			)
			continue
		}
		successCount++
	}
	return successCount
}

func (s *larkOutboundService) processOutbox(outboxID int64) error {
	outbox := ChannelMessageOutboxService.Get(outboxID)
	if outbox == nil {
		return nil
	}
	if outbox.ChannelType != enums.ChannelTypeLark {
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
		return s.markOutboxFailed(outbox, "lark channel not found or disabled")
	}
	cfg, err := ChannelService.ParseLarkChannelConfig(channel.ConfigJSON)
	if err != nil || cfg == nil {
		return s.markOutboxFailed(outbox, "invalid lark channel config")
	}
	if cfg.AppID == "" || cfg.AppSecret == "" {
		return s.markOutboxFailed(outbox, "lark app credentials not configured")
	}

	// Replies go back into the chat the customer wrote from; the chat id is
	// captured on the customer's own message payload at inbound time.
	targetChatID := ""
	lastCustomerMsg := repositories.MessageRepository.FindOne(sqls.DB(), sqls.NewCnd().
		Eq("conversation_id", conversation.ID).
		Eq("sender_type", enums.IMSenderTypeCustomer).
		Desc("id"))
	if lastCustomerMsg != nil && lastCustomerMsg.Payload != "" {
		var payloadMap map[string]any
		if err := json.Unmarshal([]byte(lastCustomerMsg.Payload), &payloadMap); err == nil {
			if ch, ok := payloadMap["lark_chat_id"].(string); ok && ch != "" {
				targetChatID = ch
			}
		}
	}

	if targetChatID == "" {
		return s.markOutboxFailed(outbox, "unable to resolve target lark chat")
	}

	client := lark.NewClient(cfg.Domain, cfg.AppID, cfg.AppSecret)

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	textToSend := message.Content
	if message.MessageType == enums.IMMessageTypeImage || message.MessageType == enums.IMMessageTypeAttachment {
		assetPayload, err := parseIMMessageAssetPayload(message.Payload)
		if err == nil && assetPayload != nil {
			assetPayload = hydrateIMMessageAssetPayload(assetPayload)
			if assetPayload.Provider != "" && assetPayload.StorageKey != "" {
				if provider, err := storage.NewProvider(assetPayload.Provider); err == nil {
					fileURL := provider.GetSignedURL(assetPayload.StorageKey)
					if fileURL != "" {
						if textToSend != "" {
							textToSend += "\n" + fileURL
						} else {
							textToSend = fileURL
						}
					}
				}
			}
		}
	}

	_, sendErr := client.SendMessageText(ctx, targetChatID, textToSend)
	if sendErr != nil {
		return s.markOutboxFailed(outbox, sendErr.Error())
	}

	return ChannelMessageOutboxService.Updates(outbox.ID, map[string]any{
		"send_status": string(enums.ChannelMessageOutboxStatusSent),
		"sent_at":     time.Now(),
		"updated_at":  time.Now(),
	})
}

func (s *larkOutboundService) markOutboxFailed(outbox *models.ChannelMessageOutbox, errMsg string) error {
	if outbox == nil {
		return nil
	}
	retryCount := outbox.RetryCount + 1
	status := string(enums.ChannelMessageOutboxStatusFailed)
	if retryCount >= larkOutboxMaxRetry {
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
