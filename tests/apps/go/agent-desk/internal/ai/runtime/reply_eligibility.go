package runtime

import (
	"crypto/sha256"
	"encoding/binary"
	"fmt"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"

	"github.com/mlogclub/simple/common/strs"
)

type replyEligibility struct{}

func newReplyEligibility() *replyEligibility {
	return &replyEligibility{}
}

// IsAIAgentRolloutEligible uses a stable conversation bucket so one customer
// remains consistently inside or outside a gray release throughout a session.
// Missing legacy values are treated as 100 to preserve existing behavior.
func IsAIAgentRolloutEligible(conversation models.Conversation, aiAgent models.AIAgent, channel *models.Channel) bool {
	percent := normalizedRolloutPercent(aiAgent.RolloutPercent)
	if channel != nil {
		channelPercent := normalizedRolloutPercent(channel.AIAgentRolloutPercent)
		if channelPercent < percent {
			percent = channelPercent
		}
	}
	if percent >= 100 {
		return true
	}
	if conversation.ID <= 0 {
		return false
	}
	seed := fmt.Sprintf("channel=%d;conversation=%d;agent=%d", conversation.ChannelID, conversation.ID, aiAgent.ID)
	sum := sha256.Sum256([]byte(seed))
	bucket := int(binary.BigEndian.Uint64(sum[:8]) % 100)
	return bucket < percent
}

func normalizedRolloutPercent(percent int) int {
	if percent <= 0 || percent > 100 {
		return 100
	}
	return percent
}

func (e *replyEligibility) CanReply(conversation models.Conversation, message models.Message, aiAgent models.AIAgent) bool {
	if message.SenderType != enums.IMSenderTypeCustomer {
		return false
	}
	if conversation.HandoffAt != nil || conversation.CurrentAssigneeID > 0 {
		return false
	}
	if aiAgent.ServiceMode == enums.IMConversationServiceModeHumanOnly {
		return false
	}
	if strs.IsBlank(message.Content) {
		return false
	}
	return true
}
