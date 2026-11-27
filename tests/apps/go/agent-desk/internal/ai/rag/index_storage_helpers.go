package rag

import (
	"context"
	"fmt"
	"log/slog"

	"agent-desk/internal/ai/rag/vectordb"
)

func (s *index) ensureCollection(ctx context.Context, provider vectordb.Provider, collectionName string, dimension int) error {
	collectionInfo, err := provider.GetCollection(ctx, collectionName)
	if err == nil && collectionInfo != nil {
		return nil
	}
	if dimension <= 0 {
		return fmt.Errorf("invalid embedding dimension: %d", dimension)
	}
	if err := provider.CreateCollection(ctx, collectionName, dimension); err != nil {
		return fmt.Errorf("failed to create collection: %w", err)
	}
	slog.Info("Created collection for knowledge base", "collection", collectionName, "dimension", dimension)
	return nil
}
