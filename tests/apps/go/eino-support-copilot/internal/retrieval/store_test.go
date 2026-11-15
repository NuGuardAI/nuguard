package retrieval

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/testsupport"
	"github.com/jackc/pgx/v5/pgxpool"
	pgvector "github.com/pgvector/pgvector-go"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestHybridSearchReturnsTraceableExactEvidence(t *testing.T) {
	pool := testsupport.StartPostgres(t)
	embedder := NewMockEmbedder(1536)
	exactID := insertKnowledgeChunk(t, pool, embedder,
		"subscription-activation.md",
		"Activation",
		"The field subscription_sync_status can remain processing for up to fifteen minutes.",
	)
	insertKnowledgeChunk(t, pool, embedder,
		"general-plan-guide.md",
		"Plan updates",
		"A plan upgrade may need extra time before it appears on the account.",
	)
	searcher := NewSearcher(NewStore(pool), embedder, 0.02)

	citations, err := searcher.Search(context.Background(), "subscription_sync_status processing", 6)

	require.NoError(t, err)
	require.NotEmpty(t, citations)
	assert.Equal(t, exactID, citations[0].ChunkID)
	for _, citation := range citations {
		assert.NotEqual(t, uuid.Nil, citation.ChunkID)
		assert.NotEmpty(t, citation.Title)
		assert.NotEmpty(t, citation.Locator)
		assert.NotEmpty(t, citation.Content)
	}
}

func TestHybridSearchReturnsNoWeakVectorOnlyEvidence(t *testing.T) {
	pool := testsupport.StartPostgres(t)
	embedder := NewMockEmbedder(1536)
	insertKnowledgeChunk(t, pool, embedder,
		"subscription-activation.md",
		"Activation",
		"A paid account can wait for subscription processing.",
	)
	searcher := NewSearcher(NewStore(pool), embedder, 0.02)

	citations, err := searcher.Search(context.Background(), "unrelated-galaxy-term", 6)

	require.NoError(t, err)
	assert.Empty(t, citations)
}

func insertKnowledgeChunk(
	t *testing.T,
	pool *pgxpool.Pool,
	embedder Embedder,
	filename string,
	locator string,
	content string,
) uuid.UUID {
	t.Helper()
	vectors, err := embedder.Embed(context.Background(), []string{content})
	require.NoError(t, err)
	documentID := uuid.New()
	chunkID := uuid.New()
	require.NoError(t, pool.QueryRow(context.Background(), `
		insert into knowledge_documents (id, filename, media_type, sha256, status)
		values ($1, $2, 'text/markdown', $3, 'ready')
		returning id
	`, documentID, filename, uuid.NewString()).Scan(&documentID))
	require.NoError(t, pool.QueryRow(context.Background(), `
		insert into knowledge_chunks (id, document_id, ordinal, content, locator, embedding)
		values ($1, $2, 0, $3, $4, $5)
		returning id
	`, chunkID, documentID, content, locator, pgvector.NewVector(vectors[0])).Scan(&chunkID))
	return chunkID
}
