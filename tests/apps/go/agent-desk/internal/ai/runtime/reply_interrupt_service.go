package runtime

import (
	"context"
	"fmt"
	"strings"

	applicationruntime "agent-desk/internal/ai/application/runtime"
	"agent-desk/internal/ai/runtime/graphs"
	svc "agent-desk/internal/services"
)

type replyInterruptService struct{}

func newReplyInterruptService() *replyInterruptService {
	return &replyInterruptService{}
}

func (s *replyInterruptService) ResumePendingInterrupt(ctx context.Context, owner *aiReplyService, replyCtx aiReplyContext) error {
	if replyCtx.PendingInterrupt == nil {
		return fmt.Errorf("pending interrupt is required")
	}
	summary, err := owner.executor.ResumePendingInterrupt(ctx, runtimeReplyResumeInput{
		Conversation:     replyCtx.Conversation,
		Message:          replyCtx.Message,
		AIAgent:          replyCtx.AIAgent,
		PendingInterrupt: replyCtx.PendingInterrupt,
	})
	replyCtx.setSummary(summary)
	if err != nil {
		if isCheckpointMissingError(err) {
			summary = expiredInterruptSummary()
			replyCtx.setSummary(summary)
			replyMessage, expireErr := owner.commit.CommitAIReply(replyCommitInput{
				Conversation:  replyCtx.Conversation,
				Message:       replyCtx.Message,
				AIAgent:       replyCtx.AIAgent,
				ReplyText:     summary.ReplyText,
				ClientPrefix:  "ai_interrupt_expired",
				WorkflowRunID: summary.WorkflowRunID,
			})
			if expireErr != nil {
				return expireErr
			}
			lastResumeMessageID := int64(0)
			if replyMessage != nil {
				lastResumeMessageID = replyMessage.ID
			}
			if expireMarkErr := svc.ConversationInterruptService.MarkExpired(replyCtx.PendingInterrupt.ID, lastResumeMessageID); expireMarkErr != nil {
				return expireMarkErr
			}
			return nil
		}
		return err
	}
	if summary != nil && summary.Interrupted {
		return s.HandleInterruptedResume(owner, replyCtx, summary)
	}
	if summary != nil && strings.TrimSpace(summary.ReplyText) != "" {
		replyMessage, err := owner.commit.CommitAIReply(replyCommitInput{
			Conversation:  replyCtx.Conversation,
			Message:       replyCtx.Message,
			AIAgent:       replyCtx.AIAgent,
			ReplyText:     summary.ReplyText,
			ClientPrefix:  "ai_resume",
			WorkflowRunID: summary.WorkflowRunID,
		})
		if err != nil {
			return err
		}
		replyMessageID := int64(0)
		if replyMessage != nil {
			replyMessageID = replyMessage.ID
		}
		if graphs.IsCancellationReply(summary.ReplyText) {
			return svc.ConversationInterruptService.MarkCancelled(replyCtx.PendingInterrupt.ID, replyMessageID)
		}
		return svc.ConversationInterruptService.MarkResolved(replyCtx.PendingInterrupt.ID, replyMessageID)
	}
	return svc.ConversationInterruptService.MarkResolved(replyCtx.PendingInterrupt.ID, 0)
}

func (s *replyInterruptService) HandleInterruptedSummary(owner *aiReplyService, replyCtx aiReplyContext, summary *applicationruntime.RunResult) error {
	pending := buildConversationInterrupt(replyCtx.Conversation, replyCtx.Message, replyCtx.AIAgent, summary)
	if pending != nil && pending.AgentRunID > 0 {
		pending.AgentStepID = svc.AgentRunService.GetLatestStepID(pending.AgentRunID)
	}
	if err := svc.ConversationInterruptService.CreateOrUpdatePending(pending); err != nil {
		return err
	}
	pending = svc.ConversationInterruptService.GetByCheckPointID(summary.CheckPointID)
	replyText := resolveInterruptPrompt(summary)
	replyMessage, err := owner.commit.CommitAIReply(replyCommitInput{
		Conversation:  replyCtx.Conversation,
		Message:       replyCtx.Message,
		AIAgent:       replyCtx.AIAgent,
		ReplyText:     replyText,
		ClientPrefix:  "ai_interrupt",
		WorkflowRunID: summary.WorkflowRunID,
	})
	if err != nil {
		return err
	}
	if replyMessage != nil && pending != nil {
		return svc.ConversationInterruptService.MarkPendingAgain(pending.ID, pending.InterruptID, replyText, replyMessage.ID)
	}
	return nil
}

func (s *replyInterruptService) HandleInterruptedResume(owner *aiReplyService, replyCtx aiReplyContext, summary *applicationruntime.RunResult) error {
	if replyCtx.PendingInterrupt == nil {
		return fmt.Errorf("pending interrupt is required")
	}
	replyText := resolveInterruptPrompt(summary)
	replyMessage, err := owner.commit.CommitAIReply(replyCommitInput{
		Conversation:  replyCtx.Conversation,
		Message:       replyCtx.Message,
		AIAgent:       replyCtx.AIAgent,
		ReplyText:     replyText,
		ClientPrefix:  "ai_interrupt_resume",
		WorkflowRunID: summary.WorkflowRunID,
	})
	if err != nil {
		return err
	}
	if replyMessage != nil {
		return svc.ConversationInterruptService.MarkPendingAgain(replyCtx.PendingInterrupt.ID, firstInterruptID(summary), replyText, replyMessage.ID)
	}
	return nil
}
