package chunk

import (
	"agent-desk/internal/pkg/enums"
	"context"
)

type Provider interface {
	Name() string
	Supports(contentType enums.KnowledgeDocumentContentType) bool
	Chunk(ctx context.Context, req *ChunkRequest) ([]ChunkResult, error)
}
