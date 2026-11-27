package services

import (
	"context"
	"log/slog"
	"strings"
	"time"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"
	"agent-desk/internal/zalo"

	"github.com/mlogclub/simple/sqls"
)

const (
	zaloOAOutboxBatchSize = 20
	zaloOAOutboxMaxRetry  = 5
)

var ZaloOAOutboundService = newZaloOAOutboundService()

func newZaloOAOutboundService() *zaloOAOutboundService {
	return &zaloOAOutboundService{}
}

type zaloOAOutboundService struct{}

func (s *zaloOAOutboundService) DispatchPendingOutbox() int {
	return s.doDispatchPendingOutbox(zaloOAOutboxBatchSize)
}

func (s *zaloOAOutboundService) doDispatchPendingOutbox(limit int) int {
	if limit <= 0 {
		limit = zaloOAOutboxBatchSize
	}
	items := ChannelMessageOutboxService.ListPending(enums.ChannelTypeZaloOA, limit)
	if len(items) == 0 {
		return 0
	}

	successCount := 0
	for i := range items {
		if err := s.processOutbox(items[i].ID); err != nil {
			slog.Warn("process zalo oa outbox failed",
				"outbox_id", items[i].ID,
				"error", err,
			)
			continue
		}
		successCount++
	}
	return successCount
}

func (s *zaloOAOutboundService) processOutbox(outboxID int64) error {
	outbox := ChannelMessageOutboxService.Get(outboxID)
	if outbox == nil {
		return nil
	}
	if outbox.ChannelType != enums.ChannelTypeZaloOA {
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
		return s.markOutboxFailed(outbox, "zalo oa channel not found or disabled")
	}
	cfg, err := ChannelService.ParseZaloOAChannelConfig(channel.ConfigJSON)
	if err != nil || cfg == nil || cfg.AccessToken == "" {
		return s.markOutboxFailed(outbox, "zalo oa access token not configured")
	}

	// Resolve target Zalo User ID
	var zaloUserID string
	customerIdentity := repositories.CustomerIdentityRepository.FindOne(sqls.DB(), sqls.NewCnd().
		Eq("customer_id", conversation.CustomerID).
		Eq("external_source", enums.ExternalSourceZaloOA))
	if customerIdentity != nil {
		zaloUserID = strings.TrimSpace(customerIdentity.ExternalID)
	}
	if zaloUserID == "" {
		return s.markOutboxFailed(outbox, "unable to resolve zalo user_id")
	}

	// Send message via Zalo Client
	client := zalo.NewClient(cfg.AccessToken)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	_, sendErr := client.SendCSMessage(ctx, zaloUserID, message.Content)
	if sendErr != nil {
		return s.markOutboxFailed(outbox, sendErr.Error())
	}

	return ChannelMessageOutboxService.Updates(outbox.ID, map[string]any{
		"send_status": string(enums.ChannelMessageOutboxStatusSent),
		"sent_at":     time.Now(),
		"updated_at":  time.Now(),
	})
}

func (s *zaloOAOutboundService) markOutboxFailed(outbox *models.ChannelMessageOutbox, errMsg string) error {
	if outbox == nil {
		return nil
	}
	retryCount := outbox.RetryCount + 1
	status := string(enums.ChannelMessageOutboxStatusFailed)
	if retryCount >= zaloOAOutboxMaxRetry {
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
