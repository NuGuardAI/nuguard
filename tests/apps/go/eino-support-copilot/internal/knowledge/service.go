package knowledge

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"path/filepath"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/retrieval"
)

const MaxUploadBytes int64 = 10 << 20

var (
	ErrUnsupportedMediaType = errors.New("unsupported media type")
	ErrFileTooLarge         = errors.New("file too large")
	ErrContentTypeMismatch  = errors.New("content type mismatch")
	ErrDuplicateDocument    = errors.New("duplicate document")
	ErrInvalidUpload        = errors.New("invalid upload")
)

type Upload struct {
	Filename  string
	MediaType string
	Size      int64
	Reader    io.Reader
}

type IndexedChunk struct {
	Ordinal   int
	Content   string
	Locator   string
	Embedding []float32
}

type Repository interface {
	Create(context.Context, domain.KnowledgeDocument) error
	UpdateStatus(context.Context, uuid.UUID, domain.KnowledgeStatus, *string) error
	ReplaceChunksAndMarkReady(context.Context, uuid.UUID, []IndexedChunk) error
	List(context.Context) ([]domain.KnowledgeDocument, error)
}

type Service struct {
	repository Repository
	chunker    *retrieval.Chunker
	embedder   retrieval.Embedder
}

func NewService(repository Repository, chunker *retrieval.Chunker, embedder retrieval.Embedder) *Service {
	return &Service{repository: repository, chunker: chunker, embedder: embedder}
}

func (s *Service) Upload(ctx context.Context, upload Upload) (domain.KnowledgeDocument, error) {
	upload.Filename = strings.TrimSpace(filepath.Base(upload.Filename))
	upload.MediaType = strings.TrimSpace(strings.Split(upload.MediaType, ";")[0])
	if err := validateDeclaredType(upload.Filename, upload.MediaType); err != nil {
		return domain.KnowledgeDocument{}, err
	}
	if upload.Size > MaxUploadBytes {
		return domain.KnowledgeDocument{}, ErrFileTooLarge
	}
	if upload.Reader == nil {
		return domain.KnowledgeDocument{}, ErrInvalidUpload
	}
	data, err := io.ReadAll(io.LimitReader(upload.Reader, MaxUploadBytes+1))
	if err != nil {
		return domain.KnowledgeDocument{}, fmt.Errorf("read upload: %w", err)
	}
	if int64(len(data)) > MaxUploadBytes {
		return domain.KnowledgeDocument{}, ErrFileTooLarge
	}
	if err := validateDetectedType(upload.Filename, upload.MediaType, data); err != nil {
		return domain.KnowledgeDocument{}, err
	}

	digest := sha256.Sum256(data)
	now := time.Now().UTC()
	document := domain.KnowledgeDocument{
		ID:        uuid.New(),
		Filename:  upload.Filename,
		MediaType: upload.MediaType,
		SHA256:    hex.EncodeToString(digest[:]),
		Status:    domain.KnowledgeUploaded,
		CreatedAt: now,
		UpdatedAt: now,
	}
	if s.repository == nil || s.chunker == nil || s.embedder == nil {
		return domain.KnowledgeDocument{}, ErrInvalidUpload
	}
	if err := s.repository.Create(ctx, document); err != nil {
		return domain.KnowledgeDocument{}, err
	}
	if err := s.repository.UpdateStatus(ctx, document.ID, domain.KnowledgeIndexing, nil); err != nil {
		return domain.KnowledgeDocument{}, err
	}
	document.Status = domain.KnowledgeIndexing
	document.UpdatedAt = time.Now().UTC()

	parsed, err := retrieval.ParseDocument(document.Filename, document.MediaType, data)
	if err != nil {
		return domain.KnowledgeDocument{}, s.failIndexing(ctx, document.ID, err)
	}
	chunks, err := s.chunker.Split(parsed)
	if err != nil {
		return domain.KnowledgeDocument{}, s.failIndexing(ctx, document.ID, err)
	}
	texts := make([]string, len(chunks))
	for index := range chunks {
		texts[index] = chunks[index].Content
	}
	vectors, err := s.embedder.Embed(ctx, texts)
	if err != nil {
		return domain.KnowledgeDocument{}, s.failIndexing(ctx, document.ID, err)
	}
	if len(vectors) != len(chunks) {
		return domain.KnowledgeDocument{}, s.failIndexing(ctx, document.ID, retrieval.ErrEmbeddingDimension)
	}
	indexed := make([]IndexedChunk, len(chunks))
	for index, chunk := range chunks {
		indexed[index] = IndexedChunk{
			Ordinal:   chunk.Ordinal,
			Content:   chunk.Content,
			Locator:   chunk.Locator,
			Embedding: vectors[index],
		}
	}
	if err := s.repository.ReplaceChunksAndMarkReady(ctx, document.ID, indexed); err != nil {
		return domain.KnowledgeDocument{}, s.failIndexing(ctx, document.ID, err)
	}
	document.Status = domain.KnowledgeReady
	document.UpdatedAt = time.Now().UTC()
	return document, nil
}

func (s *Service) List(ctx context.Context) ([]domain.KnowledgeDocument, error) {
	if s.repository == nil {
		return nil, ErrInvalidUpload
	}
	return s.repository.List(ctx)
}

func (s *Service) failIndexing(ctx context.Context, id uuid.UUID, cause error) error {
	message := "indexing failed"
	statusCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
	defer cancel()
	if updateErr := s.repository.UpdateStatus(statusCtx, id, domain.KnowledgeFailed, &message); updateErr != nil {
		return fmt.Errorf("index document: %w; update failure status: %v", cause, updateErr)
	}
	return cause
}

func validateDeclaredType(filename, mediaType string) error {
	switch strings.ToLower(filepath.Ext(filename)) {
	case ".md":
		if mediaType != "text/markdown" && mediaType != "text/plain" {
			return ErrUnsupportedMediaType
		}
	case ".txt":
		if mediaType != "text/plain" {
			return ErrUnsupportedMediaType
		}
	case ".pdf":
		if mediaType != "application/pdf" {
			return ErrUnsupportedMediaType
		}
	default:
		return ErrUnsupportedMediaType
	}
	return nil
}

func validateDetectedType(filename, mediaType string, data []byte) error {
	if len(data) == 0 {
		return ErrContentTypeMismatch
	}
	if strings.EqualFold(filepath.Ext(filename), ".pdf") {
		if !strings.HasPrefix(string(data), "%PDF-") {
			return ErrContentTypeMismatch
		}
		return nil
	}
	detected := http.DetectContentType(data)
	if mediaType == "text/plain" || mediaType == "text/markdown" {
		if !strings.HasPrefix(detected, "text/plain") {
			return ErrContentTypeMismatch
		}
		return nil
	}
	return ErrContentTypeMismatch
}
