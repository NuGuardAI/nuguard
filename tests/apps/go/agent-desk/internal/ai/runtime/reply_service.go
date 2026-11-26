package runtime

import (
	"strings"

	applicationruntime "agent-desk/internal/ai/application/runtime"
	svc "agent-desk/internal/services"
)

var AIReplyService = newAIReplyService()

func init() {
	svc.TriggerAIReplyAsyncHook = AIReplyService.TriggerReplyAsync
}

func newAIReplyService() *aiReplyService {
	return &aiReplyService{
		eligibility: newReplyEligibility(),
		executor:    newRuntimeReplyExecutor(),
		interrupts:  newReplyInterruptService(),
		commit:      newReplyCommitService(),
	}
}

type aiReplyService struct {
	eligibility *replyEligibility
	executor    *runtimeReplyExecutor
	interrupts  *replyInterruptService
	commit      *replyCommitService
}

func firstInvokedToolCode(summary *applicationruntime.RunResult) string {
	if summary == nil {
		return ""
	}
	if len(summary.InvokedToolCodes) > 0 {
		return strings.TrimSpace(summary.InvokedToolCodes[0])
	}
	return ""
}
