package runtime

import (
	applicationruntime "agent-desk/internal/ai/application/runtime"
	"agent-desk/internal/models"
)

type aiReplyContext struct {
	Conversation     models.Conversation
	Message          models.Message
	AIAgent          models.AIAgent
	SummaryRef       **applicationruntime.RunResult
	PendingInterrupt *models.ConversationInterrupt
}

func (c aiReplyContext) setSummary(summary *applicationruntime.RunResult) {
	if c.SummaryRef != nil {
		*c.SummaryRef = summary
	}
}
