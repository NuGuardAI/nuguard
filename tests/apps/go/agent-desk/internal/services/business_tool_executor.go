package services

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	aitooling "agent-desk/internal/ai/tooling"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/toolx"
)

// BusinessToolExecutor is the write boundary for built-in business tools.
// AgentDesk services remain the write boundary. Workflow nodes invoke this
// executor only after their human-confirm node has completed.
var BusinessToolExecutor = newBusinessToolExecutor(aitooling.DefaultRegistry)

type BusinessToolInput struct {
	Conversation   models.Conversation
	AIAgent        models.AIAgent
	ToolCode       string
	Arguments      map[string]any
	IdempotencyKey string
	Confirmed      bool
}

type BusinessToolResult struct {
	Definition aitooling.Definition
	ResultData string
	Reused     bool
}

type businessToolExecutor struct {
	registry *aitooling.Registry
}

func newBusinessToolExecutor(registry *aitooling.Registry) *businessToolExecutor {
	return &businessToolExecutor{registry: registry}
}

func (e *businessToolExecutor) Execute(_ context.Context, input BusinessToolInput) (*BusinessToolResult, error) {
	toolCode := toolx.NormalizeToolCodeAlias(strings.TrimSpace(input.ToolCode))
	definition, err := e.registry.Resolve(toolCode)
	if err != nil {
		return nil, err
	}
	if err := e.registry.Authorize(definition, aitooling.Policy{AllowedToolCodes: []string{definition.Code}, AllowedRiskLevels: []string{aitooling.RiskLevelWrite}, Confirmed: input.Confirmed}); err != nil {
		return nil, err
	}
	if input.Conversation.ID <= 0 || strings.TrimSpace(input.IdempotencyKey) == "" {
		return nil, fmt.Errorf("business tool invocation requires conversation and idempotency key")
	}
	claim, err := AgentToolInvocationService.Claim(input.Conversation.ID, input.AIAgent.ID, definition.Code, input.IdempotencyKey)
	if err != nil {
		return nil, err
	}
	if claim == nil || claim.Item == nil {
		return nil, fmt.Errorf("business tool invocation could not be claimed")
	}
	if claim.Completed {
		return &BusinessToolResult{Definition: definition, ResultData: claim.Item.ResultData, Reused: true}, nil
	}
	if !claim.Acquired {
		return nil, fmt.Errorf("business tool invocation is already running: %s", definition.Code)
	}

	resultData, err := e.execute(definition.Code, input)
	if err != nil {
		_ = AgentToolInvocationService.Fail(claim.Item, err)
		return nil, err
	}
	if err := AgentToolInvocationService.Complete(claim.Item, resultData); err != nil {
		return nil, err
	}
	return &BusinessToolResult{Definition: definition, ResultData: resultData}, nil
}

func (e *businessToolExecutor) execute(toolCode string, input BusinessToolInput) (string, error) {
	switch toolCode {
	case toolx.GraphCreateTicketConfirm.Code:
		item, err := TicketService.CreateFromConversation(request.CreateTicketFromConversationRequest{
			ConversationID:    input.Conversation.ID,
			Title:             businessToolString(input.Arguments["title"]),
			Description:       businessToolString(input.Arguments["description"]),
			TagIDs:            businessToolInt64Slice(input.Arguments["tagIds"]),
			CurrentAssigneeID: businessToolInt64(input.Arguments["assigneeId"]),
		}, businessToolPrincipal(input.AIAgent))
		if err != nil {
			return "", err
		}
		return businessToolJSON(map[string]any{"ticketId": item.ID, "ticketNo": item.TicketNo, "created": true})
	case toolx.GraphHandoffConversation.Code:
		result, err := ConversationHumanDispatchService.HandoffByAIWithRequestID(input.Conversation.ID, input.AIAgent, businessToolString(input.Arguments["reason"]), input.IdempotencyKey)
		if err != nil {
			return "", err
		}
		return businessToolJSON(map[string]any{"decision": result.Decision, "teamId": result.TeamID, "assigneeId": result.AssigneeID, "message": result.Message})
	default:
		return "", fmt.Errorf("business tool is not executable: %s", toolCode)
	}
}

func businessToolString(value any) string {
	text, _ := value.(string)
	return strings.TrimSpace(text)
}

func businessToolInt64(value any) int64 {
	switch typed := value.(type) {
	case int64:
		return typed
	case int:
		return int64(typed)
	case float64:
		return int64(typed)
	default:
		return 0
	}
}

func businessToolInt64Slice(value any) []int64 {
	switch typed := value.(type) {
	case []int64:
		return typed
	case []any:
		ret := make([]int64, 0, len(typed))
		for _, item := range typed {
			if id := businessToolInt64(item); id > 0 {
				ret = append(ret, id)
			}
		}
		return ret
	default:
		return nil
	}
}

func businessToolPrincipal(agent models.AIAgent) *dto.AuthPrincipal {
	name := strings.TrimSpace(agent.Name)
	if name == "" {
		name = "AI"
	}
	return &dto.AuthPrincipal{Username: name, Nickname: name}
}

func businessToolJSON(value any) (string, error) {
	data, err := json.Marshal(value)
	return string(data), err
}
