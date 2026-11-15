package ticket

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/conversation"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/runtrace"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var (
	ErrNoReviewableRun     = errors.New("no waiting review run found")
	ErrReviewerForbidden   = errors.New("agent reviewer role required")
	ErrFinalAnswerRequired = errors.New("final answer required")
	ErrInvalidReviewAction = errors.New("invalid review action")
)

type ReviewCommand struct {
	TicketID    uuid.UUID
	ReviewerID  uuid.UUID
	Action      domain.ReviewAction
	FinalAnswer string
}

type Detail struct {
	Ticket       domain.Ticket       `json:"ticket"`
	Conversation domain.Conversation `json:"conversation"`
	LatestRun    *domain.AgentRun    `json:"latest_run"`
	Citations    []domain.Citation   `json:"citations"`
	Facts        []domain.Fact       `json:"facts"`
}

type Service struct {
	pool       *pgxpool.Pool
	repository *Repository
}

func NewService(pool *pgxpool.Pool) *Service {
	return &Service{pool: pool, repository: NewRepository(pool)}
}

func (s *Service) CreateFromConversation(ctx context.Context, conversationID uuid.UUID) (domain.Ticket, error) {
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return domain.Ticket{}, fmt.Errorf("begin ticket creation: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	var existingID uuid.UUID
	err = tx.QueryRow(ctx, `
		select id from tickets
		where conversation_id = $1 and status in ('open', 'triaged', 'waiting_review')
		order by created_at limit 1
	`, conversationID).Scan(&existingID)
	if err == nil {
		ticketRecord, getErr := NewRepository(tx).Get(ctx, existingID)
		if getErr != nil {
			return domain.Ticket{}, getErr
		}
		if err := tx.Commit(ctx); err != nil {
			return domain.Ticket{}, err
		}
		return ticketRecord, nil
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return domain.Ticket{}, fmt.Errorf("find active ticket: %w", err)
	}

	var runID uuid.UUID
	var rawDraft string
	err = tx.QueryRow(ctx, `
		select id, output_summary
		from agent_runs
		where conversation_id = $1 and status = 'waiting_review' and output_summary is not null
		order by created_at desc limit 1
		for update
	`, conversationID).Scan(&runID, &rawDraft)
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.Ticket{}, ErrNoReviewableRun
	}
	if err != nil {
		return domain.Ticket{}, fmt.Errorf("load reviewable run: %w", err)
	}
	var draft domain.Draft
	if err := json.Unmarshal([]byte(rawDraft), &draft); err != nil {
		return domain.Ticket{}, fmt.Errorf("decode reviewable draft: %w", err)
	}
	summary := strings.TrimSpace(draft.Answer)
	if len([]rune(summary)) > 200 {
		summary = string([]rune(summary)[:200]) + "…"
	}
	ticketRecord, err := NewRepository(tx).Create(ctx, CreateParams{
		ConversationID: conversationID,
		Status:         domain.TicketWaitingReview,
		Priority:       draft.Priority,
		Intent:         draft.Intent,
		Summary:        summary,
		Draft:          &draft,
	})
	if err != nil {
		return domain.Ticket{}, err
	}
	if _, err := tx.Exec(ctx, `update agent_runs set ticket_id = $2 where id = $1`, runID, ticketRecord.ID); err != nil {
		return domain.Ticket{}, fmt.Errorf("link run to ticket: %w", err)
	}
	if err := tx.Commit(ctx); err != nil {
		return domain.Ticket{}, fmt.Errorf("commit ticket creation: %w", err)
	}
	return ticketRecord, nil
}

func (s *Service) Review(ctx context.Context, command ReviewCommand) (domain.TicketReview, error) {
	command.FinalAnswer = strings.TrimSpace(command.FinalAnswer)
	switch command.Action {
	case domain.ReviewApprove, domain.ReviewEditAndApprove:
		if command.FinalAnswer == "" {
			return domain.TicketReview{}, ErrFinalAnswerRequired
		}
	case domain.ReviewEscalate:
	default:
		return domain.TicketReview{}, ErrInvalidReviewAction
	}

	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return domain.TicketReview{}, fmt.Errorf("begin ticket review: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	var role string
	if err := tx.QueryRow(ctx, `select role from demo_users where id = $1`, command.ReviewerID).Scan(&role); err != nil || role != "agent" {
		return domain.TicketReview{}, ErrReviewerForbidden
	}
	var conversationID uuid.UUID
	var status domain.TicketStatus
	if err := tx.QueryRow(ctx, `select conversation_id, status from tickets where id = $1 for update`, command.TicketID).Scan(&conversationID, &status); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return domain.TicketReview{}, ErrNotFound
		}
		return domain.TicketReview{}, err
	}
	if status != domain.TicketWaitingReview {
		return domain.TicketReview{}, ErrInvalidTransition
	}
	var review domain.TicketReview
	if err := tx.QueryRow(ctx, `
		insert into ticket_reviews (ticket_id, reviewer_id, action, final_answer)
		values ($1, $2, $3, nullif($4, ''))
		returning id, ticket_id, reviewer_id, action, final_answer, created_at
	`, command.TicketID, command.ReviewerID, command.Action, command.FinalAnswer).Scan(
		&review.ID, &review.TicketID, &review.ReviewerID, &review.Action, &review.FinalAnswer, &review.CreatedAt,
	); err != nil {
		return domain.TicketReview{}, err
	}
	if command.Action == domain.ReviewEscalate {
		if _, err := tx.Exec(ctx, `update tickets set status = 'escalated', updated_at = now() where id = $1`, command.TicketID); err != nil {
			return domain.TicketReview{}, err
		}
	} else {
		if _, err := tx.Exec(ctx, `
			insert into messages (conversation_id, role, content)
			values ($1, 'agent', $2)
		`, conversationID, command.FinalAnswer); err != nil {
			return domain.TicketReview{}, err
		}
		if _, err := tx.Exec(ctx, `update tickets set status = 'resolved', updated_at = now() where id = $1`, command.TicketID); err != nil {
			return domain.TicketReview{}, err
		}
		if _, err := tx.Exec(ctx, `
			update agent_runs set status = 'completed', completed_at = now()
			where ticket_id = $1 and status = 'waiting_review'
		`, command.TicketID); err != nil {
			return domain.TicketReview{}, err
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return domain.TicketReview{}, fmt.Errorf("commit ticket review: %w", err)
	}
	return review, nil
}

func (s *Service) Get(ctx context.Context, id uuid.UUID) (domain.Ticket, error) {
	return s.repository.Get(ctx, id)
}

func (s *Service) GetDetail(ctx context.Context, id uuid.UUID) (Detail, error) {
	ticketRecord, err := s.repository.Get(ctx, id)
	if err != nil {
		return Detail{}, err
	}
	conversationRecord, err := conversation.NewRepository(s.pool).Get(ctx, ticketRecord.ConversationID)
	if err != nil {
		return Detail{}, fmt.Errorf("get ticket conversation: %w", err)
	}

	var latestRun *domain.AgentRun
	var latestRunID uuid.UUID
	err = s.pool.QueryRow(ctx, `
		select id from agent_runs
		where conversation_id = $1
		order by created_at desc, id desc limit 1
	`, ticketRecord.ConversationID).Scan(&latestRunID)
	if err == nil {
		run, getErr := runtrace.NewRepository(s.pool).Get(ctx, latestRunID)
		if getErr != nil {
			return Detail{}, fmt.Errorf("get latest ticket run: %w", getErr)
		}
		latestRun = &run
	} else if !errors.Is(err, pgx.ErrNoRows) {
		return Detail{}, fmt.Errorf("find latest ticket run: %w", err)
	}

	citations := []domain.Citation{}
	facts := []domain.Fact{}
	if ticketRecord.Draft != nil {
		citations = ticketRecord.Draft.Citations
		facts = ticketRecord.Draft.Facts
	}
	return Detail{
		Ticket: ticketRecord, Conversation: conversationRecord, LatestRun: latestRun,
		Citations: citations, Facts: facts,
	}, nil
}

func (s *Service) List(ctx context.Context, filter ListFilter) ([]domain.Ticket, error) {
	return s.repository.List(ctx, filter)
}
