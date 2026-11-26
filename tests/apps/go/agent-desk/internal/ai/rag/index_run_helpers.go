package rag

import (
	"context"
	"fmt"
	"log/slog"

	"agent-desk/internal/ai"
	"agent-desk/internal/ai/rag/vectordb"
	"agent-desk/internal/models"
	"agent-desk/internal/repositories"

	"github.com/mlogclub/simple/sqls"
)

func (s *index) runDocumentIndex(ctx context.Context, document models.KnowledgeDocument, knowledgeBase models.KnowledgeBase) ([]vectordb.Vector, int, error) {
	existingChunks := repositories.KnowledgeChunkRepository.FindByDocumentID(sqls.DB(), document.ID)
	chunks, err := s.buildDocumentChunks(ctx, document, knowledgeBase)
	if err != nil {
		return nil, 0, err
	}

	collectionName := s.getCollectionName()
	provider := vectordb.GetProvider()
	if provider == nil {
		return nil, 0, fmt.Errorf("vectordb provider not initialized")
	}
	if _, err := ai.Embedding.GetModel(ctx); err != nil {
		return nil, 0, fmt.Errorf("failed to get embedding model: %w", err)
	}

	vectors, chunkModels, dimension, err := s.prepareDocumentVectors(ctx, knowledgeBase, document, chunks)
	if err != nil {
		return nil, 0, err
	}
	if err := s.ensureCollection(ctx, provider, collectionName, dimension); err != nil {
		return nil, 0, err
	}
	if err := provider.UpsertVectors(ctx, collectionName, vectors); err != nil {
		return nil, 0, fmt.Errorf("failed to upsert vectors: %w", err)
	}
	if err := repositories.KnowledgeChunkRepository.ReplaceByDocumentID(sqls.DB(), document.ID, chunkModels); err != nil {
		return nil, 0, fmt.Errorf("failed to save chunks: %w", err)
	}
	if staleVectorIDs := s.collectStaleVectorIDs(existingChunks, vectors); len(staleVectorIDs) > 0 {
		if err := provider.DeleteVectors(ctx, collectionName, staleVectorIDs); err != nil {
			slog.Error("Failed to delete stale document vectors", "document_id", document.ID, "error", err)
		}
	}
	return vectors, len(chunks), nil
}

func (s *index) runFAQIndex(ctx context.Context, faq models.KnowledgeFAQ, knowledgeBase models.KnowledgeBase) error {
	existingChunks := repositories.KnowledgeChunkRepository.FindByFaqID(sqls.DB(), faq.ID)
	content := buildFAQChunkContent(faq)
	if content == "" {
		return fmt.Errorf("faq content is empty")
	}

	provider := vectordb.GetProvider()
	if provider == nil {
		return fmt.Errorf("vectordb provider not initialized")
	}
	if _, err := ai.Embedding.GetModel(ctx); err != nil {
		return fmt.Errorf("failed to get embedding model: %w", err)
	}
	vector, chunkModel, dimension, err := s.prepareFAQVector(ctx, knowledgeBase, faq, content)
	if err != nil {
		return err
	}

	collectionName := s.getCollectionName()
	if err := s.ensureCollection(ctx, provider, collectionName, dimension); err != nil {
		return err
	}

	if err := provider.UpsertVectors(ctx, collectionName, []vectordb.Vector{vector}); err != nil {
		return fmt.Errorf("failed to upsert vectors: %w", err)
	}
	if err := repositories.KnowledgeChunkRepository.ReplaceByFaqID(sqls.DB(), faq.ID, &chunkModel); err != nil {
		return fmt.Errorf("failed to save faq chunk: %w", err)
	}
	if staleVectorIDs := s.collectStaleVectorIDs(existingChunks, []vectordb.Vector{vector}); len(staleVectorIDs) > 0 {
		if err := provider.DeleteVectors(ctx, collectionName, staleVectorIDs); err != nil {
			slog.Error("Failed to delete stale faq vectors", "faq_id", faq.ID, "error", err)
		}
	}
	return nil
}

func (s *index) collectStaleVectorIDs(existingChunks []models.KnowledgeChunk, currentVectors []vectordb.Vector) []string {
	currentVectorIDs := make(map[string]struct{}, len(currentVectors))
	for _, vector := range currentVectors {
		if vector.ID == "" {
			continue
		}
		currentVectorIDs[vector.ID] = struct{}{}
	}
	staleVectorIDs := make([]string, 0, len(existingChunks))
	for _, chunk := range existingChunks {
		if chunk.VectorID == "" {
			continue
		}
		if _, ok := currentVectorIDs[chunk.VectorID]; ok {
			continue
		}
		staleVectorIDs = append(staleVectorIDs, chunk.VectorID)
	}
	return staleVectorIDs
}
