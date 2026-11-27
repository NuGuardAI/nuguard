package runtime

import (
	"context"
	"fmt"
	"strings"

	applicationruntime "agent-desk/internal/ai/application/runtime"
	"agent-desk/internal/ai/runtime/graphs"
	"agent-desk/internal/models"
)

type runtimeReplyExecutor struct{}

type runtimeReplyRunInput struct {
	Conversation models.Conversation
	Message      models.Message
	AIAgent      models.AIAgent
}

type runtimeReplyResumeInput struct {
	Conversation     models.Conversation
	Message          models.Message
	AIAgent          models.AIAgent
	PendingInterrupt *models.ConversationInterrupt
}

func newRuntimeReplyExecutor() *runtimeReplyExecutor {
	return &runtimeReplyExecutor{}
}

func (e *runtimeReplyExecutor) Run(ctx context.Context, input runtimeReplyRunInput) (*applicationruntime.RunResult, error) {
	summary, err := applicationruntime.DefaultAgentApplicationService.Run(ctx, applicationruntime.ApplicationRunInput{
		ConversationID: input.Conversation.ID,
		MessageID:      input.Message.ID,
		AIAgentID:      input.AIAgent.ID,
	})
	return summary, err
}

func (e *runtimeReplyExecutor) ResumePendingInterrupt(ctx context.Context, input runtimeReplyResumeInput) (*applicationruntime.RunResult, error) {
	if input.PendingInterrupt == nil {
		return nil, fmt.Errorf("pending interrupt is required")
	}
	summary, err := applicationruntime.DefaultAgentApplicationService.Resume(ctx, applicationruntime.ApplicationResumeInput{
		ApplicationRunInput: applicationruntime.ApplicationRunInput{
			ConversationID: input.Conversation.ID,
			MessageID:      input.Message.ID,
			AIAgentID:      input.AIAgent.ID,
		},
		CheckPointID: strings.TrimSpace(input.PendingInterrupt.CheckPointID),
		ResumeData: map[string]string{
			strings.TrimSpace(input.PendingInterrupt.InterruptID): strings.TrimSpace(input.Message.Content),
		},
	})
	return summary, err
}

func expiredInterruptSummary() *applicationruntime.RunResult {
	return &applicationruntime.RunResult{
		Status:    "expired",
		ReplyText: graphs.ConfirmationExpiredReply,
	}
}
