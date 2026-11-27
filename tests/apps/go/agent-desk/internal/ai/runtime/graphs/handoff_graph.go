package graphs

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"agent-desk/internal/ai/runtime/tooling"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/i18nx"
	"agent-desk/internal/pkg/tracex"
	"agent-desk/internal/services"

	componenttool "github.com/cloudwego/eino/components/tool"
	"github.com/cloudwego/eino/schema"
)

type HandoffGraphState struct {
	Reason string `json:"reason"`
}

type HandoffGraphInterruptInfo struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}

type handoffGraphArgs struct {
	Reason string `json:"reason"`
}

func init() {
	schema.RegisterName[HandoffGraphState]("cs_ai_agent_handoff_graph_state")
	schema.RegisterName[HandoffGraphInterruptInfo]("cs_ai_agent_handoff_graph_interrupt_info")
}

type HandoffGraph struct {
	conversation models.Conversation
	aiAgent      models.AIAgent
}

func NewHandoffGraph(conversation models.Conversation, aiAgent models.AIAgent) *HandoffGraph {
	return &HandoffGraph{
		conversation: conversation,
		aiAgent:      aiAgent,
	}
}

func (g *HandoffGraph) Run(ctx context.Context, argumentsInJSON string) (string, error) {
	wasInterrupted, hasState, state := componenttool.GetInterruptState[HandoffGraphState](ctx)
	if !wasInterrupted {
		reason, err := g.buildReason(argumentsInJSON)
		if err != nil {
			return "", err
		}
		requestID := tracex.RequestIDFromContext(ctx)
		handled, err := services.ConversationService.TryOffHoursHandoffByAIWithRequestID(g.conversation.ID, g.aiAgent, reason, requestID)
		if err != nil || handled {
			if handled && err == nil {
				return tooling.MarshalToolResult(tooling.ToolResult{
					Handled:     true,
					Terminal:    true,
					Action:      "off_hours_handoff",
					ReplyText:   services.HandoffOffHoursMessage,
					ReplySent:   true,
					ShouldRetry: false,
				}), nil
			}
			return "", err
		}
		info := HandoffGraphInterruptInfo{
			Type:    InterruptTypeHandoffConfirmation,
			Message: g.buildConfirmationPrompt(reason),
		}
		return "", componenttool.StatefulInterrupt(ctx, info, HandoffGraphState{Reason: reason})
	}
	if !hasState {
		return "", fmt.Errorf("handoff graph state missing")
	}
	isResumeTarget, hasData, resumeText := componenttool.GetResumeContext[string](ctx)
	if !isResumeTarget {
		info := HandoffGraphInterruptInfo{
			Type:    InterruptTypeHandoffConfirmation,
			Message: g.buildConfirmationPrompt(state.Reason),
		}
		return "", componenttool.StatefulInterrupt(ctx, info, state)
	}
	if !hasData {
		info := HandoffGraphInterruptInfo{
			Type:    InterruptTypeHandoffConfirmation,
			Message: ConfirmOrCancelPrompt,
		}
		return "", componenttool.StatefulInterrupt(ctx, info, state)
	}
	switch parseHandoffDecision(resumeText) {
	case ConfirmationDecisionConfirm:
		if err := services.ConversationService.HandoffByAIWithRequestID(g.conversation.ID, g.aiAgent, state.Reason, tracex.RequestIDFromContext(ctx)); err != nil {
			return "", err
		}
		// ConversationService sends the customer-visible handoff notice according to the dispatch decision.
		return tooling.MarshalToolResult(tooling.ToolResult{
			Handled:     true,
			Terminal:    true,
			Action:      "handoff_confirmed",
			ReplySent:   true,
			ShouldRetry: false,
		}), nil
	case ConfirmationDecisionCancel:
		return tooling.MarshalToolResult(tooling.ToolResult{
			Handled:     true,
			Terminal:    true,
			Action:      "handoff_cancelled",
			ReplyText:   CancelHandoffReply,
			ShouldRetry: false,
		}), nil
	default:
		info := HandoffGraphInterruptInfo{
			Type:    InterruptTypeHandoffConfirmation,
			Message: NeedExplicitConfirmationPrompt,
		}
		return "", componenttool.StatefulInterrupt(ctx, info, state)
	}
}

func (g *HandoffGraph) buildReason(argumentsInJSON string) (string, error) {
	reason := i18nx.Get("graph.defaultHandoffReason")
	var args handoffGraphArgs
	if strings.TrimSpace(argumentsInJSON) != "" {
		if err := json.Unmarshal([]byte(argumentsInJSON), &args); err != nil {
			return "", fmt.Errorf("invalid handoff arguments: %w", err)
		}
	}
	if parsed := strings.TrimSpace(args.Reason); parsed != "" {
		reason = parsed
	}
	return reason, nil
}

func (g *HandoffGraph) buildConfirmationPrompt(reason string) string {
	return i18nx.Getf(i18nx.DefaultLocale, "graph.handoffConfirmPrompt", strings.TrimSpace(reason))
}

func parseHandoffDecision(value string) ConfirmationDecision {
	return ParseConfirmationDecision(value)
}
