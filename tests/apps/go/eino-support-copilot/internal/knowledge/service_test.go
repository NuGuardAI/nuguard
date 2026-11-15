package knowledge

import (
	"context"
	"io"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/retrieval"
	"github.com/guiyangyuan/eino-support-copilot/internal/testsupport"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestUploadRejectsExecutableOversizedAndDisguisedFiles(t *testing.T) {
	service := NewService(nil, retrieval.NewChunker(600, 0.15), retrieval.NewMockEmbedder(1536))

	_, err := service.Upload(context.Background(), Upload{
		Filename: "run.sh", MediaType: "text/x-shellscript", Size: 7, Reader: strings.NewReader("echo hi"),
	})
	assert.ErrorIs(t, err, ErrUnsupportedMediaType)

	_, err = service.Upload(context.Background(), Upload{
		Filename: "large.md", MediaType: "text/markdown", Size: MaxUploadBytes + 1, Reader: strings.NewReader("x"),
	})
	assert.ErrorIs(t, err, ErrFileTooLarge)

	_, err = service.Upload(context.Background(), Upload{
		Filename: "guide.pdf", MediaType: "application/pdf", Size: 7, Reader: strings.NewReader("echo hi"),
	})
	assert.ErrorIs(t, err, ErrContentTypeMismatch)
}

func TestUploadLimitsActualBytesWhenDeclaredSizeIsWrong(t *testing.T) {
	service := NewService(nil, retrieval.NewChunker(600, 0.15), retrieval.NewMockEmbedder(1536))
	reader := io.LimitReader(strings.NewReader(strings.Repeat("x", int(MaxUploadBytes+1))), MaxUploadBytes+1)

	_, err := service.Upload(context.Background(), Upload{
		Filename: "large.txt", MediaType: "text/plain", Size: 1, Reader: reader,
	})

	assert.ErrorIs(t, err, ErrFileTooLarge)
}

func TestUploadPersistsStatusSequenceChunksAndDuplicateGuard(t *testing.T) {
	pool := testsupport.StartPostgres(t)
	repository := &recordingRepository{inner: NewPostgresRepository(pool)}
	service := NewService(repository, retrieval.NewChunker(600, 0.15), retrieval.NewMockEmbedder(1536))
	upload := Upload{
		Filename:  "activation.md",
		MediaType: "text/markdown",
		Size:      57,
		Reader:    strings.NewReader("# Activation\n\nA paid plan activates within five minutes."),
	}

	document, err := service.Upload(context.Background(), upload)

	require.NoError(t, err)
	assert.Equal(t, domain.KnowledgeReady, document.Status)
	assert.Equal(t, []domain.KnowledgeStatus{
		domain.KnowledgeUploaded,
		domain.KnowledgeIndexing,
		domain.KnowledgeReady,
	}, repository.statuses)
	var chunkCount int
	require.NoError(t, pool.QueryRow(context.Background(),
		`select count(*) from knowledge_chunks where document_id = $1`, document.ID,
	).Scan(&chunkCount))
	assert.Greater(t, chunkCount, 0)

	_, err = service.Upload(context.Background(), Upload{
		Filename:  upload.Filename,
		MediaType: upload.MediaType,
		Size:      upload.Size,
		Reader:    strings.NewReader("# Activation\n\nA paid plan activates within five minutes."),
	})
	assert.ErrorIs(t, err, ErrDuplicateDocument)
}

func TestUploadRollsBackChunksWhenEmbeddingDimensionIsInvalid(t *testing.T) {
	pool := testsupport.StartPostgres(t)
	repository := NewPostgresRepository(pool)
	service := NewService(repository, retrieval.NewChunker(600, 0.15), embedderFunc(
		func(_ context.Context, texts []string) ([][]float32, error) {
			vectors := make([][]float32, len(texts))
			for index := range vectors {
				vectors[index] = []float32{1, 2, 3}
			}
			return vectors, nil
		},
	))

	_, err := service.Upload(context.Background(), Upload{
		Filename:  "invalid-dimension.md",
		MediaType: "text/markdown",
		Size:      28,
		Reader:    strings.NewReader("# Guide\n\nGenerated content."),
	})

	require.Error(t, err)
	var status domain.KnowledgeStatus
	var chunkCount int
	require.NoError(t, pool.QueryRow(context.Background(), `
		select status, (select count(*) from knowledge_chunks kc where kc.document_id = kd.id)
		from knowledge_documents kd
		where filename = 'invalid-dimension.md'
	`).Scan(&status, &chunkCount))
	assert.Equal(t, domain.KnowledgeFailed, status)
	assert.Zero(t, chunkCount)
}

func TestUploadRecordsFailureAfterRequestContextIsCanceled(t *testing.T) {
	repository := &contextRecordingRepository{}
	ctx, cancel := context.WithCancel(context.Background())
	service := NewService(repository, retrieval.NewChunker(600, 0.15), embedderFunc(
		func(ctx context.Context, _ []string) ([][]float32, error) {
			cancel()
			return nil, ctx.Err()
		},
	))

	_, err := service.Upload(ctx, Upload{
		Filename:  "canceled.md",
		MediaType: "text/markdown",
		Size:      28,
		Reader:    strings.NewReader("# Guide\n\nGenerated content."),
	})

	require.ErrorIs(t, err, context.Canceled)
	require.Len(t, repository.statusContexts, 2)
	assert.NoError(t, repository.statusContexts[0])
	assert.NoError(t, repository.statusContexts[1])
	assert.Equal(t, domain.KnowledgeFailed, repository.statuses[1])
}

type embedderFunc func(context.Context, []string) ([][]float32, error)

func (f embedderFunc) Embed(ctx context.Context, texts []string) ([][]float32, error) {
	return f(ctx, texts)
}

type recordingRepository struct {
	inner    *PostgresRepository
	statuses []domain.KnowledgeStatus
}

type contextRecordingRepository struct {
	statuses       []domain.KnowledgeStatus
	statusContexts []error
}

func (r *contextRecordingRepository) Create(context.Context, domain.KnowledgeDocument) error {
	return nil
}

func (r *contextRecordingRepository) UpdateStatus(
	ctx context.Context,
	_ uuid.UUID,
	status domain.KnowledgeStatus,
	_ *string,
) error {
	r.statuses = append(r.statuses, status)
	r.statusContexts = append(r.statusContexts, ctx.Err())
	return nil
}

func (r *contextRecordingRepository) ReplaceChunksAndMarkReady(context.Context, uuid.UUID, []IndexedChunk) error {
	return nil
}

func (r *contextRecordingRepository) List(context.Context) ([]domain.KnowledgeDocument, error) {
	return nil, nil
}

func (r *recordingRepository) Create(ctx context.Context, document domain.KnowledgeDocument) error {
	r.statuses = append(r.statuses, document.Status)
	return r.inner.Create(ctx, document)
}

func (r *recordingRepository) UpdateStatus(
	ctx context.Context,
	id uuid.UUID,
	status domain.KnowledgeStatus,
	errorMessage *string,
) error {
	r.statuses = append(r.statuses, status)
	return r.inner.UpdateStatus(ctx, id, status, errorMessage)
}

func (r *recordingRepository) ReplaceChunksAndMarkReady(
	ctx context.Context,
	documentID uuid.UUID,
	chunks []IndexedChunk,
) error {
	r.statuses = append(r.statuses, domain.KnowledgeReady)
	return r.inner.ReplaceChunksAndMarkReady(ctx, documentID, chunks)
}

func (r *recordingRepository) List(ctx context.Context) ([]domain.KnowledgeDocument, error) {
	return r.inner.List(ctx)
}
