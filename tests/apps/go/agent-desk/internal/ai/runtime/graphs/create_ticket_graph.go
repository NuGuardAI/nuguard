package graphs

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"agent-desk/internal/ai/runtime/tooling"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/i18nx"
	"agent-desk/internal/services"

	componenttool "github.com/cloudwego/eino/components/tool"
	"github.com/cloudwego/eino/schema"
)

type CreateTicketGraphState struct {
	Request request.CreateTicketFromConversationRequest
}

type CreateTicketGraphInterruptInfo struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}

type createTicketGraphArgs struct {
	Title       string `json:"title"`
	Description string `json:"description"`
}

func init() {
	schema.RegisterName[CreateTicketGraphState]("cs_ai_agent_create_ticket_graph_state")
	schema.RegisterName[CreateTicketGraphInterruptInfo]("cs_ai_agent_create_ticket_graph_interrupt_info")
}

type CreateTicketGraph struct {
	conversation models.Conversation
	aiAgent      models.AIAgent
}

func NewCreateTicketGraph(conversation models.Conversation, aiAgent models.AIAgent) *CreateTicketGraph {
	return &CreateTicketGraph{
		conversation: conversation,
		aiAgent:      aiAgent,
	}
}

func (g *CreateTicketGraph) Run(ctx context.Context, argumentsInJSON string) (string, error) {
	wasInterrupted, hasState, state := componenttool.GetInterruptState[CreateTicketGraphState](ctx)
	if !wasInterrupted {
		req, err := g.buildCreateRequest(argumentsInJSON)
		if err != nil {
			return "", err
		}
		info := CreateTicketGraphInterruptInfo{
			Type:    InterruptTypeTicketCreationConfirmation,
			Message: g.buildConfirmationPrompt(req),
		}
		return "", componenttool.StatefulInterrupt(ctx, info, CreateTicketGraphState{Request: req})
	}
	if !hasState {
		return "", fmt.Errorf("create ticket graph state missing")
	}
	isResumeTarget, hasData, resumeText := componenttool.GetResumeContext[string](ctx)
	if !isResumeTarget {
		info := CreateTicketGraphInterruptInfo{
			Type:    InterruptTypeTicketCreationConfirmation,
			Message: g.buildConfirmationPrompt(state.Request),
		}
		return "", componenttool.StatefulInterrupt(ctx, info, state)
	}
	if !hasData {
		info := CreateTicketGraphInterruptInfo{
			Type:    InterruptTypeTicketCreationConfirmation,
			Message: ConfirmOrCancelPrompt,
		}
		return "", componenttool.StatefulInterrupt(ctx, info, state)
	}
	decision := ParseConfirmationDecision(resumeText)
	switch decision {
	case ConfirmationDecisionConfirm:
		item, err := services.TicketService.CreateFromConversation(state.Request, g.buildAIPrincipal())
		if err != nil {
			return "", err
		}
		return tooling.MarshalToolResult(tooling.ToolResult{
			Handled:     true,
			Terminal:    true,
			Action:      "ticket_created",
			ReplyText:   i18nx.Getf(i18nx.DefaultLocale, "graph.ticketCreated", strings.TrimSpace(item.TicketNo), strings.TrimSpace(item.Title)),
			ShouldRetry: false,
		}), nil
	case ConfirmationDecisionCancel:
		return tooling.MarshalToolResult(tooling.ToolResult{
			Handled:     true,
			Terminal:    true,
			Action:      "ticket_cancelled",
			ReplyText:   CancelCreateTicketReply,
			ShouldRetry: false,
		}), nil
	default:
		info := CreateTicketGraphInterruptInfo{
			Type:    InterruptTypeTicketCreationConfirmation,
			Message: NeedExplicitConfirmationPrompt,
		}
		return "", componenttool.StatefulInterrupt(ctx, info, state)
	}
}

func (g *CreateTicketGraph) buildCreateRequest(argumentsInJSON string) (request.CreateTicketFromConversationRequest, error) {
	req := request.CreateTicketFromConversationRequest{
		ConversationID: g.conversation.ID,
	}
	var args createTicketGraphArgs
	if strings.TrimSpace(argumentsInJSON) != "" {
		if err := json.Unmarshal([]byte(argumentsInJSON), &args); err != nil {
			return req, fmt.Errorf("invalid create ticket arguments: %w", err)
		}
	}
	req.Title = strings.TrimSpace(args.Title)
	req.Description = strings.TrimSpace(args.Description)
	if req.Title == "" {
		req.Title = strings.TrimSpace(g.conversation.LastMessageSummary)
	}
	if req.Description == "" {
		req.Description = strings.TrimSpace(g.conversation.LastMessageSummary)
	}
	if strings.TrimSpace(req.Title) == "" {
		return req, fmt.Errorf("ticket title is required")
	}
	return req, nil
}

func (g *CreateTicketGraph) buildConfirmationPrompt(req request.CreateTicketFromConversationRequest) string {
	return i18nx.Getf(i18nx.DefaultLocale, "graph.createTicketConfirmPrompt",
		strings.TrimSpace(req.Title), strings.TrimSpace(req.Description))
}

func (g *CreateTicketGraph) buildAIPrincipal() *dto.AuthPrincipal {
	username := "AI"
	if strings.TrimSpace(g.aiAgent.Name) != "" {
		username = strings.TrimSpace(g.aiAgent.Name)
	}
	return &dto.AuthPrincipal{
		UserID:   0,
		Username: username,
		Nickname: username,
	}
}
