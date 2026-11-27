package runtime

import (
	"context"
	"log/slog"
	"strings"
	"time"

	applicationruntime "agent-desk/internal/ai/application/runtime"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/tracex"
	svc "agent-desk/internal/services"
)

func (s *aiReplyService) resolveReplyTimeout(aiAgent models.AIAgent) time.Duration {
	if aiAgent.ReplyTimeoutSeconds <= 0 {
		return time.Duration(defaultAIReplyAsyncTimeoutSeconds) * time.Second
	}
	if aiAgent.ReplyTimeoutSeconds > maxAIReplyAsyncTimeoutSeconds {
		return time.Duration(maxAIReplyAsyncTimeoutSeconds) * time.Second
	}
	return time.Duration(aiAgent.ReplyTimeoutSeconds) * time.Second
}

func (s *aiReplyService) TriggerReplyAsync(conversation models.Conversation, message models.Message) {
	go func() {
		aiAgent := svc.AIAgentService.Get(conversation.AIAgentID)
		if aiAgent == nil || aiAgent.Status != enums.StatusOk {
			return
		}
		startedAt := time.Now()
		timeout := s.resolveReplyTimeout(*aiAgent)
		ctx, cancel := context.WithTimeout(tracex.ContextWithRequestID(context.Background(), message.RequestID), timeout)
		defer cancel()
		if err := s.TriggerReply(ctx, conversation, message, *aiAgent); err != nil {
			slog.Error("failed to trigger ai reply",
				"requestId", message.RequestID,
				"message_id", message.ID,
				"timeout_ms", timeout.Milliseconds(),
				"elapsed_ms", time.Since(startedAt).Milliseconds(),
				"error", err)
		}
	}()
}

func (s *aiReplyService) TriggerReply(ctx context.Context, conversation models.Conversation, message models.Message, aiAgent models.AIAgent) (retErr error) {
	var summary *applicationruntime.RunResult
	replyCtx := aiReplyContext{
		Conversation: conversation,
		Message:      message,
		AIAgent:      aiAgent,
		SummaryRef:   &summary,
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if s.eligibility != nil && !s.eligibility.CanReply(conversation, message, aiAgent) {
		return nil
	}
	if !IsAIAgentRolloutEligible(conversation, aiAgent, svc.ChannelService.Get(conversation.ChannelID)) {
		return nil
	}
	if pendingInterrupt := svc.ConversationInterruptService.FindLatestPendingByConversationID(conversation.ID); pendingInterrupt != nil {
		replyCtx.PendingInterrupt = pendingInterrupt
		return s.resumePendingInterrupt(ctx, replyCtx)
	}
	return s.executeReply(ctx, replyCtx)
}

func (s *aiReplyService) resumePendingInterrupt(ctx context.Context, replyCtx aiReplyContext) error {
	return s.interrupts.ResumePendingInterrupt(ctx, s, replyCtx)
}

func (s *aiReplyService) executeReply(ctx context.Context, replyCtx aiReplyContext) error {
	summary, err := s.executor.Run(ctx, runtimeReplyRunInput{
		Conversation: replyCtx.Conversation,
		Message:      replyCtx.Message,
		AIAgent:      replyCtx.AIAgent,
	})
	replyCtx.setSummary(summary)
	if err != nil {
		return err
	}
	if summary != nil && summary.Interrupted {
		return s.interrupts.HandleInterruptedSummary(s, replyCtx, summary)
	}
	if summary != nil && summary.HandoffRequested {
		if _, err := svc.ConversationHumanDispatchService.HandoffByAIWithRequestID(
			replyCtx.Conversation.ID,
			replyCtx.AIAgent,
			summary.HandoffReason,
			replyCtx.Message.RequestID,
		); err != nil {
			return err
		}
		return nil
	}
	if summary != nil && strings.TrimSpace(summary.ReplyText) != "" {
		_, err := s.commit.CommitAIReply(replyCommitInput{
			Conversation:  replyCtx.Conversation,
			Message:       replyCtx.Message,
			AIAgent:       replyCtx.AIAgent,
			ReplyText:     summary.ReplyText,
			ClientPrefix:  "ai_reply",
			WorkflowRunID: summary.WorkflowRunID,
		})
		if err != nil {
			return err
		}
	}
	return nil
}
