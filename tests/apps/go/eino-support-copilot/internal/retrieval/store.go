package retrieval

import (
	"context"
	"fmt"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/platform/database"
	pgvector "github.com/pgvector/pgvector-go"
)

const candidateLimit = 20

type StoredChunk struct {
	ID      uuid.UUID
	Title   string
	Locator string
	Content string
}

type Store struct {
	db database.DBTX
}

func NewStore(db database.DBTX) *Store {
	return &Store{db: db}
}

func (s *Store) KeywordIDs(ctx context.Context, query string, limit int) ([]uuid.UUID, error) {
	rows, err := s.db.Query(ctx, `
		select kc.id
		from knowledge_chunks kc
		where kc.search_vector @@ websearch_to_tsquery('simple', $1)
		order by ts_rank_cd(kc.search_vector, websearch_to_tsquery('simple', $1)) desc, kc.id
		limit $2
	`, query, normalizeCandidateLimit(limit))
	if err != nil {
		return nil, fmt.Errorf("keyword retrieval: %w", err)
	}
	defer rows.Close()

	ids := make([]uuid.UUID, 0)
	for rows.Next() {
		var id uuid.UUID
		if err := rows.Scan(&id); err != nil {
			return nil, fmt.Errorf("scan keyword result: %w", err)
		}
		ids = append(ids, id)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate keyword results: %w", err)
	}
	return ids, nil
}

func (s *Store) VectorIDs(ctx context.Context, vector []float32, limit int) ([]uuid.UUID, error) {
	rows, err := s.db.Query(ctx, `
		select kc.id
		from knowledge_chunks kc
		order by kc.embedding <=> $1
		limit $2
	`, pgvector.NewVector(vector), normalizeCandidateLimit(limit))
	if err != nil {
		return nil, fmt.Errorf("vector retrieval: %w", err)
	}
	defer rows.Close()

	ids := make([]uuid.UUID, 0)
	for rows.Next() {
		var id uuid.UUID
		if err := rows.Scan(&id); err != nil {
			return nil, fmt.Errorf("scan vector result: %w", err)
		}
		ids = append(ids, id)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate vector results: %w", err)
	}
	return ids, nil
}

func (s *Store) ChunksByID(ctx context.Context, ids []uuid.UUID) (map[uuid.UUID]StoredChunk, error) {
	if len(ids) == 0 {
		return map[uuid.UUID]StoredChunk{}, nil
	}
	rows, err := s.db.Query(ctx, `
		select kc.id, kd.filename, kc.locator, kc.content
		from knowledge_chunks kc
		join knowledge_documents kd on kd.id = kc.document_id
		where kc.id = any($1)
	`, ids)
	if err != nil {
		return nil, fmt.Errorf("load retrieval chunks: %w", err)
	}
	defer rows.Close()

	chunks := make(map[uuid.UUID]StoredChunk, len(ids))
	for rows.Next() {
		var chunk StoredChunk
		if err := rows.Scan(&chunk.ID, &chunk.Title, &chunk.Locator, &chunk.Content); err != nil {
			return nil, fmt.Errorf("scan retrieval chunk: %w", err)
		}
		chunks[chunk.ID] = chunk
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate retrieval chunks: %w", err)
	}
	return chunks, nil
}

func normalizeCandidateLimit(limit int) int {
	if limit <= 0 || limit > candidateLimit {
		return candidateLimit
	}
	return limit
}
