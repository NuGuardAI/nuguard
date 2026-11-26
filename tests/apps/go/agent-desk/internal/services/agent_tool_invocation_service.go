package services

import (
	"strings"
	"time"

	"agent-desk/internal/models"
	"agent-desk/internal/repositories"

	"github.com/mlogclub/simple/sqls"
)

const (
	agentToolInvocationStatusRunning   = "running"
	agentToolInvocationStatusCompleted = "completed"
	agentToolInvocationStatusFailed    = "failed"
)

var AgentToolInvocationService = newAgentToolInvocationService()

type AgentToolInvocationClaim struct {
	Item      *models.AgentToolInvocation
	Completed bool
	Acquired  bool
}

type agentToolInvocationService struct{}

func newAgentToolInvocationService() *agentToolInvocationService {
	return &agentToolInvocationService{}
}

// Claim obtains the persistent idempotency boundary. A completed invocation
// can be returned to callers; an in-flight invocation is never executed again.
func (s *agentToolInvocationService) Claim(conversationID, aiAgentID int64, toolCode, idempotencyKey string) (*AgentToolInvocationClaim, error) {
	toolCode = strings.TrimSpace(toolCode)
	idempotencyKey = strings.TrimSpace(idempotencyKey)
	if conversationID <= 0 || toolCode == "" || idempotencyKey == "" {
		return nil, nil
	}
	if item := repositories.AgentToolInvocationRepository.GetByIdempotencyKey(sqls.DB(), conversationID, toolCode, idempotencyKey); item != nil {
		if item.Status == agentToolInvocationStatusCompleted {
			return &AgentToolInvocationClaim{Item: item, Completed: true}, nil
		}
		if item.Status == agentToolInvocationStatusRunning {
			return &AgentToolInvocationClaim{Item: item}, nil
		}
		if err := repositories.AgentToolInvocationRepository.Updates(sqls.DB(), item.ID, map[string]any{"status": agentToolInvocationStatusRunning, "error_message": "", "updated_at": time.Now()}); err != nil {
			return nil, err
		}
		item.Status, item.ErrorMessage = agentToolInvocationStatusRunning, ""
		return &AgentToolInvocationClaim{Item: item, Acquired: true}, nil
	}
	item := &models.AgentToolInvocation{ConversationID: conversationID, AIAgentID: aiAgentID, ToolCode: toolCode, IdempotencyKey: idempotencyKey, Status: agentToolInvocationStatusRunning}
	if err := repositories.AgentToolInvocationRepository.Create(sqls.DB(), item); err != nil {
		// A concurrent caller may have created the unique invocation first.
		if existing := repositories.AgentToolInvocationRepository.GetByIdempotencyKey(sqls.DB(), conversationID, toolCode, idempotencyKey); existing != nil {
			return &AgentToolInvocationClaim{Item: existing, Completed: existing.Status == agentToolInvocationStatusCompleted}, nil
		}
		return nil, err
	}
	return &AgentToolInvocationClaim{Item: item, Acquired: true}, nil
}

func (s *agentToolInvocationService) Complete(item *models.AgentToolInvocation, resultData string) error {
	if item == nil || item.ID <= 0 {
		return nil
	}
	return repositories.AgentToolInvocationRepository.Updates(sqls.DB(), item.ID, map[string]any{"status": agentToolInvocationStatusCompleted, "result_data": resultData, "error_message": "", "updated_at": time.Now()})
}

func (s *agentToolInvocationService) Fail(item *models.AgentToolInvocation, cause error) error {
	if item == nil || item.ID <= 0 {
		return nil
	}
	message := ""
	if cause != nil {
		message = cause.Error()
	}
	return repositories.AgentToolInvocationRepository.Updates(sqls.DB(), item.ID, map[string]any{"status": agentToolInvocationStatusFailed, "error_message": message, "updated_at": time.Now()})
}
