package runtime

import (
	"context"
	"strings"

	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	svc "agent-desk/internal/services"
)

// ApplicationRunInput identifies the persisted inputs for an Agent reply.
// Loading these records here keeps channels and debug adapters independent of
// individual engine requirements.
type ApplicationRunInput struct {
	ConversationID int64
	MessageID      int64
	AIAgentID      int64
}

type ApplicationResumeInput struct {
	ApplicationRunInput
	CheckPointID string
	ResumeData   map[string]string
}

// AgentApplicationService is the single application boundary before engine
// dispatch. It owns persisted input loading and relationship validation; the
// Agent Loop remains responsible only for runtime execution.
type AgentApplicationService struct {
	runtime *Service
}

var DefaultAgentApplicationService = NewAgentApplicationService()

func NewAgentApplicationService() *AgentApplicationService {
	return &AgentApplicationService{runtime: NewService()}
}

func (s *AgentApplicationService) Run(ctx context.Context, input ApplicationRunInput) (*RunResult, error) {
	req, err := s.loadRequest(input)
	if err != nil {
		return nil, err
	}
	return s.RunPrepared(ctx, req)
}

// RunPrepared is for isolated adapters such as the dashboard debug session.
// Callers are responsible for constructing an ephemeral or already-validated
// request; no persistence side effects are introduced by this boundary.
func (s *AgentApplicationService) RunPrepared(ctx context.Context, req RunInput) (*RunResult, error) {
	return s.runtime.Run(ctx, req)
}

func (s *AgentApplicationService) Resume(ctx context.Context, input ApplicationResumeInput) (*RunResult, error) {
	req, err := s.loadRequest(input.ApplicationRunInput)
	if err != nil {
		return nil, err
	}
	checkPointID := strings.TrimSpace(input.CheckPointID)
	interrupt := svc.ConversationInterruptService.GetByCheckPointID(checkPointID)
	if interrupt == nil || interrupt.ConversationID != req.Conversation.ID {
		return nil, errorsx.InvalidParam("pending conversation interrupt does not exist")
	}
	if interrupt.AIAgentID > 0 && interrupt.AIAgentID != req.AIAgent.ID {
		return nil, errorsx.InvalidParam("interrupt does not belong to agent")
	}
	return s.ResumePrepared(ctx, ResumeInput{
		Conversation: req.Conversation,
		UserMessage:  req.UserMessage,
		AIAgent:      req.AIAgent,
		AIConfig:     req.AIConfig,
		CheckPointID: checkPointID,
		ResumeData:   input.ResumeData,
	})
}

func (s *AgentApplicationService) ResumePrepared(ctx context.Context, req ResumeInput) (*RunResult, error) {
	return s.runtime.Resume(ctx, req)
}

func (s *AgentApplicationService) loadRequest(input ApplicationRunInput) (RunInput, error) {
	if input.ConversationID <= 0 || input.MessageID <= 0 || input.AIAgentID <= 0 {
		return RunInput{}, errorsx.InvalidParam("conversation, message and agent are required")
	}
	conversation := svc.ConversationService.Get(input.ConversationID)
	if conversation == nil {
		return RunInput{}, errorsx.InvalidParam("conversation does not exist")
	}
	message := svc.MessageService.Get(input.MessageID)
	if message == nil || message.ConversationID != conversation.ID {
		return RunInput{}, errorsx.InvalidParam("message does not belong to conversation")
	}
	agent := svc.AIAgentService.Get(input.AIAgentID)
	if agent == nil || agent.Status != enums.StatusOk {
		return RunInput{}, errorsx.InvalidParam("ai agent is unavailable")
	}
	if conversation.AIAgentID > 0 && conversation.AIAgentID != agent.ID {
		return RunInput{}, errorsx.InvalidParam("agent does not belong to conversation")
	}
	config := svc.AIConfigService.Get(agent.AIConfigID)
	if config == nil || config.Status != enums.StatusOk {
		return RunInput{}, errorsx.InvalidParam("ai config is unavailable")
	}
	return RunInput{Conversation: *conversation, UserMessage: *message, AIAgent: *agent, AIConfig: *config}, nil
}
