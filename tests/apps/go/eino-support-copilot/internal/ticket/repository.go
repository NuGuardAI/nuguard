package ticket

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/platform/database"
	"github.com/jackc/pgx/v5"
)

var (
	ErrNotFound          = errors.New("ticket not found")
	ErrInvalidTransition = errors.New("invalid ticket state transition")
)

type CreateParams struct {
	ConversationID uuid.UUID
	Status         domain.TicketStatus
	Priority       domain.Priority
	Intent         string
	Summary        string
	Draft          *domain.Draft
}

type ReviewParams struct {
	TicketID    uuid.UUID
	ReviewerID  uuid.UUID
	Action      domain.ReviewAction
	FinalAnswer string
}

type ListFilter struct {
	Status   *domain.TicketStatus
	Priority *domain.Priority
}

type Repository struct {
	db database.DBTX
}

func NewRepository(db database.DBTX) *Repository {
	return &Repository{db: db}
}

func (r *Repository) Create(ctx context.Context, params CreateParams) (domain.Ticket, error) {
	var draftJSON []byte
	var err error
	if params.Draft != nil {
		draftJSON, err = json.Marshal(params.Draft)
		if err != nil {
			return domain.Ticket{}, fmt.Errorf("encode ticket draft: %w", err)
		}
	}

	var ticket domain.Ticket
	var storedDraft []byte
	err = r.db.QueryRow(ctx, `
		insert into tickets (conversation_id, status, priority, intent, summary, draft)
		values ($1, $2, $3, $4, $5, $6)
		returning id, conversation_id, status, priority, intent, summary, draft, created_at, updated_at
	`, params.ConversationID, params.Status, params.Priority, params.Intent, params.Summary, draftJSON).Scan(
		&ticket.ID,
		&ticket.ConversationID,
		&ticket.Status,
		&ticket.Priority,
		&ticket.Intent,
		&ticket.Summary,
		&storedDraft,
		&ticket.CreatedAt,
		&ticket.UpdatedAt,
	)
	if err != nil {
		return domain.Ticket{}, fmt.Errorf("create ticket: %w", err)
	}
	if err := decodeDraft(storedDraft, &ticket.Draft); err != nil {
		return domain.Ticket{}, err
	}
	ticket.Reviews = []domain.TicketReview{}
	return ticket, nil
}

func (r *Repository) Get(ctx context.Context, id uuid.UUID) (domain.Ticket, error) {
	ticket, err := r.getTicket(ctx, id)
	if err != nil {
		return domain.Ticket{}, err
	}

	rows, err := r.db.Query(ctx, `
		select id, ticket_id, reviewer_id, action, final_answer, created_at
		from ticket_reviews where ticket_id = $1
		order by created_at, id
	`, id)
	if err != nil {
		return domain.Ticket{}, fmt.Errorf("list ticket reviews: %w", err)
	}
	defer rows.Close()
	ticket.Reviews = []domain.TicketReview{}
	for rows.Next() {
		var review domain.TicketReview
		if err := rows.Scan(
			&review.ID,
			&review.TicketID,
			&review.ReviewerID,
			&review.Action,
			&review.FinalAnswer,
			&review.CreatedAt,
		); err != nil {
			return domain.Ticket{}, fmt.Errorf("scan ticket review: %w", err)
		}
		ticket.Reviews = append(ticket.Reviews, review)
	}
	if err := rows.Err(); err != nil {
		return domain.Ticket{}, fmt.Errorf("iterate ticket reviews: %w", err)
	}
	return ticket, nil
}

func (r *Repository) List(ctx context.Context, filter ListFilter) ([]domain.Ticket, error) {
	var status any
	if filter.Status != nil {
		status = string(*filter.Status)
	}
	var priority any
	if filter.Priority != nil {
		priority = string(*filter.Priority)
	}
	rows, err := r.db.Query(ctx, `
		select id, conversation_id, status, priority, intent, summary, draft, created_at, updated_at
		from tickets
		where ($1::text is null or status = $1)
		  and ($2::text is null or priority = $2)
		order by case priority when 'P0' then 0 when 'P1' then 1 when 'P2' then 2 else 3 end,
		         created_at, id
	`, status, priority)
	if err != nil {
		return nil, fmt.Errorf("list tickets: %w", err)
	}
	defer rows.Close()

	tickets := make([]domain.Ticket, 0)
	for rows.Next() {
		var ticket domain.Ticket
		var draftJSON []byte
		if err := rows.Scan(
			&ticket.ID,
			&ticket.ConversationID,
			&ticket.Status,
			&ticket.Priority,
			&ticket.Intent,
			&ticket.Summary,
			&draftJSON,
			&ticket.CreatedAt,
			&ticket.UpdatedAt,
		); err != nil {
			return nil, fmt.Errorf("scan ticket: %w", err)
		}
		if err := decodeDraft(draftJSON, &ticket.Draft); err != nil {
			return nil, err
		}
		ticket.Reviews = []domain.TicketReview{}
		tickets = append(tickets, ticket)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate tickets: %w", err)
	}
	return tickets, nil
}

func (r *Repository) Review(ctx context.Context, params ReviewParams) (domain.TicketReview, error) {
	nextStatus := domain.TicketResolved
	if params.Action == domain.ReviewEscalate {
		nextStatus = domain.TicketEscalated
	}

	var review domain.TicketReview
	err := r.db.QueryRow(ctx, `
		with target as (
			select id from tickets where id = $1 and status = 'waiting_review' for update
		), inserted as (
			insert into ticket_reviews (ticket_id, reviewer_id, action, final_answer)
			select id, $2, $3, nullif(btrim($4), '') from target
			returning id, ticket_id, reviewer_id, action, final_answer, created_at
		), updated as (
			update tickets t set status = $5, updated_at = now()
			from inserted i where t.id = i.ticket_id returning t.id
		)
		select i.id, i.ticket_id, i.reviewer_id, i.action, i.final_answer, i.created_at
		from inserted i join updated u on u.id = i.ticket_id
	`, params.TicketID, params.ReviewerID, params.Action, params.FinalAnswer, nextStatus).Scan(
		&review.ID,
		&review.TicketID,
		&review.ReviewerID,
		&review.Action,
		&review.FinalAnswer,
		&review.CreatedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		var exists bool
		lookupErr := r.db.QueryRow(ctx, `select exists(select 1 from tickets where id = $1)`, params.TicketID).Scan(&exists)
		if lookupErr != nil {
			return domain.TicketReview{}, fmt.Errorf("check ticket after review rejection: %w", lookupErr)
		}
		if !exists {
			return domain.TicketReview{}, ErrNotFound
		}
		return domain.TicketReview{}, ErrInvalidTransition
	}
	if err != nil {
		return domain.TicketReview{}, fmt.Errorf("review ticket: %w", err)
	}
	return review, nil
}

func (r *Repository) getTicket(ctx context.Context, id uuid.UUID) (domain.Ticket, error) {
	var ticket domain.Ticket
	var draftJSON []byte
	err := r.db.QueryRow(ctx, `
		select id, conversation_id, status, priority, intent, summary, draft, created_at, updated_at
		from tickets where id = $1
	`, id).Scan(
		&ticket.ID,
		&ticket.ConversationID,
		&ticket.Status,
		&ticket.Priority,
		&ticket.Intent,
		&ticket.Summary,
		&draftJSON,
		&ticket.CreatedAt,
		&ticket.UpdatedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.Ticket{}, ErrNotFound
	}
	if err != nil {
		return domain.Ticket{}, fmt.Errorf("get ticket: %w", err)
	}
	if err := decodeDraft(draftJSON, &ticket.Draft); err != nil {
		return domain.Ticket{}, err
	}
	return ticket, nil
}

func decodeDraft(raw []byte, destination **domain.Draft) error {
	if len(raw) == 0 {
		*destination = nil
		return nil
	}
	var draft domain.Draft
	if err := json.Unmarshal(raw, &draft); err != nil {
		return fmt.Errorf("decode ticket draft: %w", err)
	}
	*destination = &draft
	return nil
}
