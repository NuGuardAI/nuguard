package conversation

import (
	"context"
	"encoding/json"
	"errors"
	"strings"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/agent"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/runtrace"
)

var (
	ErrForbidden       = errors.New("conversation access forbidden")
	ErrInvalidArgument = errors.New("invalid conversation argument")
)

type SendResult struct {
	Draft domain.Draft
	Run   domain.AgentRun
}

type Service struct {
	conversations *Repository
	runs          *runtrace.Repository
	engine        agent.Engine
	mode          string
}

func NewService(conversations *Repository, runs *runtrace.Repository, engine agent.Engine) *Service {
	return NewServiceWithMode(conversations, runs, engine, "mock")
}

func NewServiceWithMode(conversations *Repository, runs *runtrace.Repository, engine agent.Engine, mode string) *Service {
	return &Service{conversations: conversations, runs: runs, engine: engine, mode: mode}
}

func (s *Service) Create(ctx context.Context, customerID uuid.UUID, title string) (domain.Conversation, error) {
	if customerID == uuid.Nil {
		return domain.Conversation{}, ErrInvalidArgument
	}
	return s.conversations.Create(ctx, customerID, title)
}

func (s *Service) List(ctx context.Context, customerID uuid.UUID) ([]domain.Conversation, error) {
	if customerID == uuid.Nil {
		return nil, ErrInvalidArgument
	}
	return s.conversations.List(ctx, customerID)
}

func (s *Service) CanAccess(ctx context.Context, customerID, conversationID uuid.UUID) error {
	conversationRecord, err := s.conversations.Get(ctx, conversationID)
	if err != nil {
		return err
	}
	if conversationRecord.CustomerID != customerID {
		return ErrForbidden
	}
	return nil
}

func (s *Service) Send(
	ctx context.Context,
	customerID uuid.UUID,
	conversationID uuid.UUID,
	text string,
	stream runtrace.Sink,
) (SendResult, error) {
	text = strings.TrimSpace(text)
	if text == "" || customerID == uuid.Nil || conversationID == uuid.Nil || stream == nil {
		return SendResult{}, ErrInvalidArgument
	}
	if err := s.CanAccess(ctx, customerID, conversationID); err != nil {
		return SendResult{}, err
	}
	message, err := s.conversations.AppendMessage(ctx, conversationID, domain.RoleUser, text)
	if err != nil {
		return SendResult{}, err
	}
	run, err := s.runs.Start(ctx, runtrace.StartParams{
		ConversationID: conversationID,
		MessageID:      &message.ID,
		Status:         domain.RunRunning,
		Mode:           s.mode,
		InputSummary:   summarizeInput(text),
	})
	if err != nil {
		return SendResult{}, err
	}
	persistentStream := runtrace.NewPersistentSink(s.runs, stream)
	draft, engineErr := s.engine.Run(ctx, agent.Input{
		ConversationID: conversationID,
		MessageID:      message.ID,
		RunID:          run.ID,
		CustomerID:     customerID,
		Text:           text,
	}, persistentStream)
	if engineErr != nil {
		status := domain.RunFailed
		if errors.Is(engineErr, context.Canceled) || errors.Is(engineErr, context.DeadlineExceeded) || ctx.Err() != nil {
			status = domain.RunCancelled
		}
		completeErr := s.runs.Complete(context.WithoutCancel(ctx), run.ID, status, "", json.RawMessage(`{}`))
		run.Status = status
		if completeErr != nil {
			return SendResult{Run: run}, errors.Join(engineErr, completeErr)
		}
		return SendResult{Run: run}, engineErr
	}

	encodedDraft, err := json.Marshal(draft)
	if err != nil {
		return SendResult{Draft: draft, Run: run}, err
	}
	status := domain.RunCompleted
	if draft.RequiresReview {
		status = domain.RunWaitingReview
	} else if _, err := s.conversations.AppendMessage(ctx, conversationID, domain.RoleAssistant, draft.Answer); err != nil {
		_ = s.runs.Complete(context.WithoutCancel(ctx), run.ID, domain.RunFailed, "", json.RawMessage(`{}`))
		run.Status = domain.RunFailed
		return SendResult{Draft: draft, Run: run}, err
	}
	if err := s.runs.Complete(ctx, run.ID, status, string(encodedDraft), json.RawMessage(`{"total_tokens":0}`)); err != nil {
		return SendResult{Draft: draft, Run: run}, err
	}
	run.Status = status
	output := string(encodedDraft)
	run.OutputSummary = &output
	return SendResult{Draft: draft, Run: run}, nil
}

func summarizeInput(text string) string {
	const maxRunes = 200
	runes := []rune(strings.TrimSpace(text))
	if len(runes) <= maxRunes {
		return string(runes)
	}
	return string(runes[:maxRunes]) + "…"
}
