package conversation

import (
	"context"
	"errors"
	"fmt"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/platform/database"
	"github.com/jackc/pgx/v5"
)

var ErrNotFound = errors.New("conversation not found")

type Repository struct {
	db database.DBTX
}

func NewRepository(db database.DBTX) *Repository {
	return &Repository{db: db}
}

func (r *Repository) Create(ctx context.Context, customerID uuid.UUID, title string) (domain.Conversation, error) {
	var conversation domain.Conversation
	err := r.db.QueryRow(ctx, `
		insert into conversations (customer_id, title)
		values ($1, coalesce(nullif(btrim($2), ''), 'New conversation'))
		returning id, customer_id, title, created_at, updated_at
	`, customerID, title).Scan(
		&conversation.ID,
		&conversation.CustomerID,
		&conversation.Title,
		&conversation.CreatedAt,
		&conversation.UpdatedAt,
	)
	if err != nil {
		return domain.Conversation{}, fmt.Errorf("create conversation: %w", err)
	}
	conversation.Messages = []domain.Message{}
	return conversation, nil
}

func (r *Repository) AppendMessage(
	ctx context.Context,
	conversationID uuid.UUID,
	role domain.MessageRole,
	content string,
) (domain.Message, error) {
	var message domain.Message
	err := r.db.QueryRow(ctx, `
		with inserted as (
			insert into messages (conversation_id, role, content)
			select id, $2, $3 from conversations where id = $1
			returning id, conversation_id, role, content, metadata, created_at
		), touched as (
			update conversations set updated_at = now()
			where id = $1 and exists (select 1 from inserted)
		)
		select id, conversation_id, role, content, metadata, created_at from inserted
	`, conversationID, role, content).Scan(
		&message.ID,
		&message.ConversationID,
		&message.Role,
		&message.Content,
		&message.Metadata,
		&message.CreatedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.Message{}, ErrNotFound
	}
	if err != nil {
		return domain.Message{}, fmt.Errorf("append message: %w", err)
	}
	return message, nil
}

func (r *Repository) Get(ctx context.Context, id uuid.UUID) (domain.Conversation, error) {
	var conversation domain.Conversation
	err := r.db.QueryRow(ctx, `
		select id, customer_id, title, created_at, updated_at
		from conversations where id = $1
	`, id).Scan(
		&conversation.ID,
		&conversation.CustomerID,
		&conversation.Title,
		&conversation.CreatedAt,
		&conversation.UpdatedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.Conversation{}, ErrNotFound
	}
	if err != nil {
		return domain.Conversation{}, fmt.Errorf("get conversation: %w", err)
	}

	rows, err := r.db.Query(ctx, `
		select id, conversation_id, role, content, metadata, created_at
		from messages where conversation_id = $1
		order by created_at, id
	`, id)
	if err != nil {
		return domain.Conversation{}, fmt.Errorf("list conversation messages: %w", err)
	}
	defer rows.Close()
	conversation.Messages = []domain.Message{}
	for rows.Next() {
		var message domain.Message
		if err := rows.Scan(
			&message.ID,
			&message.ConversationID,
			&message.Role,
			&message.Content,
			&message.Metadata,
			&message.CreatedAt,
		); err != nil {
			return domain.Conversation{}, fmt.Errorf("scan conversation message: %w", err)
		}
		conversation.Messages = append(conversation.Messages, message)
	}
	if err := rows.Err(); err != nil {
		return domain.Conversation{}, fmt.Errorf("iterate conversation messages: %w", err)
	}
	return conversation, nil
}

func (r *Repository) List(ctx context.Context, customerID uuid.UUID) ([]domain.Conversation, error) {
	rows, err := r.db.Query(ctx, `
		select id, customer_id, title, created_at, updated_at
		from conversations
		where customer_id = $1
		order by updated_at desc, id
	`, customerID)
	if err != nil {
		return nil, fmt.Errorf("list conversations: %w", err)
	}
	defer rows.Close()

	conversations := make([]domain.Conversation, 0)
	for rows.Next() {
		var conversation domain.Conversation
		if err := rows.Scan(
			&conversation.ID,
			&conversation.CustomerID,
			&conversation.Title,
			&conversation.CreatedAt,
			&conversation.UpdatedAt,
		); err != nil {
			return nil, fmt.Errorf("scan conversation: %w", err)
		}
		conversation.Messages = []domain.Message{}
		conversations = append(conversations, conversation)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate conversations: %w", err)
	}
	return conversations, nil
}
