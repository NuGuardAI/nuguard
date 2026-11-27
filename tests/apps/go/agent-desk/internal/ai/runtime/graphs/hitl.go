package graphs

import (
	"strings"

	"agent-desk/internal/pkg/i18nx"
)

const (
	InterruptTypeTicketCreationConfirmation = "ticket_creation_confirmation"
	InterruptTypeHandoffConfirmation        = "handoff_confirmation"
)

var (
	ConfirmOrCancelPrompt          = i18nx.Get("graph.confirmOrCancel")
	NeedExplicitConfirmationPrompt = i18nx.Get("graph.needExplicitConfirmation")
	ConfirmationExpiredReply       = i18nx.Get("graph.confirmationExpired")
	CancelCreateTicketReply        = i18nx.Get("graph.cancelCreateTicket")
	CancelHandoffReply             = i18nx.Get("graph.cancelHandoff")
)

type ConfirmationDecision string

const (
	ConfirmationDecisionConfirm ConfirmationDecision = "confirm"
	ConfirmationDecisionCancel  ConfirmationDecision = "cancel"
)

func ParseConfirmationDecision(value string) ConfirmationDecision {
	value = strings.ToLower(strings.TrimSpace(value))
	if value == "" {
		return ""
	}
	confirmWords := []string{"确认", "是", "好的", "可以", "ok", "yes", "continue", "confirm", "继续", "同意"}
	for _, item := range confirmWords {
		if strings.Contains(value, item) {
			return ConfirmationDecisionConfirm
		}
	}
	cancelWords := []string{"取消", "不用", "不需要", "算了", "no", "cancel"}
	for _, item := range cancelWords {
		if strings.Contains(value, item) {
			return ConfirmationDecisionCancel
		}
	}
	return ""
}

func IsCancellationReply(replyText string) bool {
	replyText = strings.TrimSpace(replyText)
	return strings.Contains(replyText, CancelCreateTicketReply) ||
		strings.Contains(replyText, CancelHandoffReply) ||
		strings.Contains(replyText, "已取消本次工单创建。") ||
		strings.Contains(replyText, "已取消本次转人工。")
}
