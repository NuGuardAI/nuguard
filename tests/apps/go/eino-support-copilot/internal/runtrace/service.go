package runtrace

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var (
	ErrForbidden         = errors.New("run access forbidden")
	ErrReplayNotAllowed  = errors.New("run cannot be replayed in its current state")
	ErrReplayUnavailable = errors.New("replay engine unavailable")
)

type ReplayInput struct {
	ConversationID uuid.UUID
	MessageID      uuid.UUID
	RunID          uuid.UUID
	CustomerID     uuid.UUID
	Text           string
}

type ReplayFunc func(context.Context, ReplayInput, Sink) (domain.Draft, error)

type Service struct {
	pool   *pgxpool.Pool
	runs   *Repository
	replay ReplayFunc
	mode   string
}

func NewService(pool *pgxpool.Pool, replay ReplayFunc, mode string) *Service {
	return &Service{pool: pool, runs: NewRepository(pool), replay: replay, mode: mode}
}

func (s *Service) Get(ctx context.Context, id, actorID uuid.UUID, role string) (domain.AgentRun, error) {
	run, err := s.runs.Get(ctx, id)
	if err != nil {
		return domain.AgentRun{}, err
	}
	if err := s.authorize(ctx, run.ConversationID, actorID, role); err != nil {
		return domain.AgentRun{}, err
	}
	redactRun(&run)
	return run, nil
}

func (s *Service) Replay(ctx context.Context, id, actorID uuid.UUID, role string) (domain.AgentRun, error) {
	if role != "agent" && role != "admin" {
		return domain.AgentRun{}, ErrForbidden
	}
	if s.replay == nil {
		return domain.AgentRun{}, ErrReplayUnavailable
	}
	original, err := s.runs.Get(ctx, id)
	if err != nil {
		return domain.AgentRun{}, err
	}
	if original.Status != domain.RunCompleted {
		return domain.AgentRun{}, ErrReplayNotAllowed
	}
	if err := s.authorize(ctx, original.ConversationID, actorID, role); err != nil {
		return domain.AgentRun{}, err
	}
	if original.MessageID == nil {
		return domain.AgentRun{}, ErrReplayNotAllowed
	}

	var text string
	var customerID uuid.UUID
	if err := s.pool.QueryRow(ctx, `
		select m.content, c.customer_id
		from messages m join conversations c on c.id = m.conversation_id
		where m.id = $1 and m.role = 'user'
	`, *original.MessageID).Scan(&text, &customerID); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return domain.AgentRun{}, ErrReplayNotAllowed
		}
		return domain.AgentRun{}, fmt.Errorf("load replay input: %w", err)
	}

	created, err := s.runs.Start(ctx, StartParams{
		ConversationID:    original.ConversationID,
		MessageID:         original.MessageID,
		Status:            domain.RunRunning,
		Mode:              s.mode,
		InputSummary:      original.InputSummary,
		ReplayedFromRunID: &original.ID,
	})
	if err != nil {
		return domain.AgentRun{}, err
	}
	input := ReplayInput{
		ConversationID: original.ConversationID,
		MessageID:      *original.MessageID,
		RunID:          created.ID,
		CustomerID:     customerID,
		Text:           text,
	}
	go s.executeReplay(context.WithoutCancel(ctx), input)
	return created, nil
}

func (s *Service) executeReplay(parent context.Context, input ReplayInput) {
	ctx, cancel := context.WithTimeout(parent, 2*time.Minute)
	defer cancel()
	draft, err := s.replay(ctx, input, NewPersistentSink(s.runs, nil))
	if err != nil {
		_ = s.runs.Fail(context.WithoutCancel(ctx), input.RunID, "REPLAY_EXECUTION_FAILED", "Replay execution failed safely.")
		return
	}
	encoded, err := json.Marshal(draft)
	if err != nil {
		_ = s.runs.Fail(context.WithoutCancel(ctx), input.RunID, "REPLAY_OUTPUT_INVALID", "Replay output could not be encoded.")
		return
	}
	status := domain.RunCompleted
	if draft.RequiresReview {
		status = domain.RunWaitingReview
	}
	if err := s.runs.Complete(context.WithoutCancel(ctx), input.RunID, status, string(encoded), json.RawMessage(`{"total_tokens":0}`)); err != nil {
		_ = s.runs.Fail(context.WithoutCancel(ctx), input.RunID, "REPLAY_PERSIST_FAILED", "Replay result could not be persisted.")
	}
}

func (s *Service) authorize(ctx context.Context, conversationID, actorID uuid.UUID, role string) error {
	if actorID == uuid.Nil {
		return ErrForbidden
	}
	if role == "agent" || role == "admin" {
		return nil
	}
	if role != "customer" {
		return ErrForbidden
	}
	var owns bool
	if err := s.pool.QueryRow(ctx, `select exists(select 1 from conversations where id = $1 and customer_id = $2)`, conversationID, actorID).Scan(&owns); err != nil {
		return fmt.Errorf("authorize run access: %w", err)
	}
	if !owns {
		return ErrForbidden
	}
	return nil
}

func redactRun(run *domain.AgentRun) {
	run.Usage = redactJSON(run.Usage)
	if run.ErrorMessage != nil {
		safe := "Run failed. Inspect the safe error code."
		run.ErrorMessage = &safe
	}
	for index := range run.Steps {
		run.Steps[index].InputSummary = redactJSON(run.Steps[index].InputSummary)
		run.Steps[index].ToolArguments = redactJSON(run.Steps[index].ToolArguments)
		run.Steps[index].ResultSummary = redactJSON(run.Steps[index].ResultSummary)
		run.Steps[index].Citations = redactJSON(run.Steps[index].Citations)
	}
}

func redactJSON(raw json.RawMessage) json.RawMessage {
	if len(raw) == 0 {
		return raw
	}
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return json.RawMessage(`{}`)
	}
	redactValue(value)
	encoded, err := json.Marshal(value)
	if err != nil {
		return json.RawMessage(`{}`)
	}
	return encoded
}

func redactValue(value any) {
	switch typed := value.(type) {
	case map[string]any:
		for key, child := range typed {
			normalized := strings.ToLower(strings.ReplaceAll(key, "-", "_"))
			if normalized == "api_key" || normalized == "token" || normalized == "password" || normalized == "authorization" || normalized == "secret" {
				typed[key] = "***"
				continue
			}
			redactValue(child)
		}
	case []any:
		for _, child := range typed {
			redactValue(child)
		}
	}
}
