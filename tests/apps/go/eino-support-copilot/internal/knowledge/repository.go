package knowledge

import (
	"context"
	"errors"
	"fmt"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	pgvector "github.com/pgvector/pgvector-go"
)

type PostgresRepository struct {
	pool *pgxpool.Pool
}

func NewPostgresRepository(pool *pgxpool.Pool) *PostgresRepository {
	return &PostgresRepository{pool: pool}
}

func (r *PostgresRepository) Create(ctx context.Context, document domain.KnowledgeDocument) error {
	_, err := r.pool.Exec(ctx, `
		insert into knowledge_documents (id, filename, media_type, sha256, status, error_message, created_at, updated_at)
		values ($1, $2, $3, $4, $5, $6, $7, $8)
	`, document.ID, document.Filename, document.MediaType, document.SHA256, document.Status,
		document.ErrorMessage, document.CreatedAt, document.UpdatedAt)
	if err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == "23505" {
			return ErrDuplicateDocument
		}
		return fmt.Errorf("create knowledge document: %w", err)
	}
	return nil
}

func (r *PostgresRepository) UpdateStatus(
	ctx context.Context,
	id uuid.UUID,
	status domain.KnowledgeStatus,
	errorMessage *string,
) error {
	tag, err := r.pool.Exec(ctx, `
		update knowledge_documents
		set status = $2, error_message = $3, updated_at = now()
		where id = $1
	`, id, status, errorMessage)
	if err != nil {
		return fmt.Errorf("update knowledge status: %w", err)
	}
	if tag.RowsAffected() != 1 {
		return pgx.ErrNoRows
	}
	return nil
}

func (r *PostgresRepository) ReplaceChunksAndMarkReady(
	ctx context.Context,
	documentID uuid.UUID,
	chunks []IndexedChunk,
) error {
	tx, err := r.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin knowledge indexing transaction: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if _, err := tx.Exec(ctx, `delete from knowledge_chunks where document_id = $1`, documentID); err != nil {
		return fmt.Errorf("replace knowledge chunks: %w", err)
	}
	for _, chunk := range chunks {
		if _, err := tx.Exec(ctx, `
			insert into knowledge_chunks (document_id, ordinal, content, locator, embedding)
			values ($1, $2, $3, $4, $5)
		`, documentID, chunk.Ordinal, chunk.Content, chunk.Locator, pgvector.NewVector(chunk.Embedding)); err != nil {
			return fmt.Errorf("insert knowledge chunk: %w", err)
		}
	}
	tag, err := tx.Exec(ctx, `
		update knowledge_documents
		set status = 'ready', error_message = null, updated_at = now()
		where id = $1
	`, documentID)
	if err != nil {
		return fmt.Errorf("mark knowledge document ready: %w", err)
	}
	if tag.RowsAffected() != 1 {
		return pgx.ErrNoRows
	}
	if err := tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit knowledge indexing: %w", err)
	}
	return nil
}

func (r *PostgresRepository) List(ctx context.Context) ([]domain.KnowledgeDocument, error) {
	rows, err := r.pool.Query(ctx, `
		select id, filename, media_type, sha256, status, error_message, created_at, updated_at
		from knowledge_documents
		order by created_at desc, id
	`)
	if err != nil {
		return nil, fmt.Errorf("list knowledge documents: %w", err)
	}
	defer rows.Close()

	documents := make([]domain.KnowledgeDocument, 0)
	for rows.Next() {
		var document domain.KnowledgeDocument
		if err := rows.Scan(
			&document.ID,
			&document.Filename,
			&document.MediaType,
			&document.SHA256,
			&document.Status,
			&document.ErrorMessage,
			&document.CreatedAt,
			&document.UpdatedAt,
		); err != nil {
			return nil, fmt.Errorf("scan knowledge document: %w", err)
		}
		documents = append(documents, document)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate knowledge documents: %w", err)
	}
	return documents, nil
}
