package rag

import (
	"context"
	"fmt"
	"log/slog"

	"agent-desk/internal/ai/rag/vectordb"
	"agent-desk/internal/models"
	"agent-desk/internal/repositories"

	"github.com/mlogclub/simple/common/strs"
	"github.com/mlogclub/simple/sqls"
)

func (s *index) collectChunkVectorIDs(chunks []models.KnowledgeChunk) []string {
	vectorIDs := make([]string, 0, len(chunks))
	for _, chunk := range chunks {
		if strs.IsNotBlank(chunk.VectorID) {
			vectorIDs = append(vectorIDs, chunk.VectorID)
		}
	}
	return vectorIDs
}

func (s *index) deleteChunkVectors(ctx context.Context, vectorIDs []string) error {
	if len(vectorIDs) == 0 {
		return nil
	}
	provider := vectordb.GetProvider()
	if provider == nil {
		return fmt.Errorf("vectordb provider not initialized")
	}
	return provider.DeleteVectors(ctx, s.getCollectionName(), vectorIDs)
}

func (s *index) cleanupKnowledgeBaseChunks(ctx context.Context, knowledgeBaseID int64, chunks []models.KnowledgeChunk) error {
	vectorIDs := s.collectChunkVectorIDs(chunks)
	if err := s.deleteChunkVectors(ctx, vectorIDs); err != nil {
		return fmt.Errorf("failed to delete vectors for knowledge base %d before rebuild: %w", knowledgeBaseID, err)
	}
	if err := repositories.KnowledgeChunkRepository.DeleteByKnowledgeBaseID(sqls.DB(), knowledgeBaseID); err != nil {
		return fmt.Errorf("failed to clear chunks before rebuild: %w", err)
	}
	slog.Info("Knowledge base index storage reset",
		"knowledge_base_id", knowledgeBaseID,
		"collection", s.getCollectionName())
	return nil
}
