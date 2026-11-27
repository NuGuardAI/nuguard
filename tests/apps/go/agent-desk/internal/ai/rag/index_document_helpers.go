package rag

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	ragchunk "agent-desk/internal/ai/rag/chunk"
	"agent-desk/internal/ai/rag/vectordb"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"

	"agent-desk/internal/ai"
)

func (s *index) buildDocumentChunkRequest(document models.KnowledgeDocument, knowledgeBase models.KnowledgeBase) *ragchunk.ChunkRequest {
	return &ragchunk.ChunkRequest{
		KnowledgeBaseID: document.KnowledgeBaseID,
		DocumentID:      document.ID,
		DocumentTitle:   document.Title,
		ContentType:     document.ContentType,
		Content:         document.Content,
		PlainText:       ExtractPlainText(document.Content, document.ContentType),
		Options: ragchunk.ChunkOptions{
			Provider:       firstNonEmptyString(knowledgeBase.ChunkProvider, s.chunkConfig.Provider),
			TargetTokens:   firstPositiveInt(knowledgeBase.ChunkTargetTokens, s.chunkConfig.TargetTokens),
			MaxTokens:      firstPositiveInt(knowledgeBase.ChunkMaxTokens, s.chunkConfig.MaxTokens),
			OverlapTokens:  firstPositiveInt(knowledgeBase.ChunkOverlapTokens, s.chunkConfig.OverlapTokens),
			EnableFallback: s.chunkConfig.EnableFallback,
		},
	}
}

func (s *index) buildDocumentChunks(ctx context.Context, document models.KnowledgeDocument, knowledgeBase models.KnowledgeBase) ([]ragchunk.ChunkResult, error) {
	chunks, err := s.registry.Chunk(ctx, s.buildDocumentChunkRequest(document, knowledgeBase))
	if err != nil {
		return nil, fmt.Errorf("failed to chunk document: %w", err)
	}
	if len(chunks) == 0 {
		return nil, fmt.Errorf("no chunks generated from document")
	}
	return chunks, nil
}

func (s *index) prepareDocumentVectors(ctx context.Context, knowledgeBase models.KnowledgeBase, document models.KnowledgeDocument, chunks []ragchunk.ChunkResult) ([]vectordb.Vector, []models.KnowledgeChunk, int, error) {
	vectors := make([]vectordb.Vector, 0, len(chunks))
	chunkModels := make([]models.KnowledgeChunk, 0, len(chunks))
	dimension := 0
	directoryPath := loadKnowledgeDirectoryPath(document.DirectoryID)

	for i, chunk := range chunks {
		embeddingResult, err := ai.Embedding.GenerateEmbedding(ctx, chunk.Content)
		if err != nil {
			slog.Error("Failed to generate embedding for chunk", "document_id", document.ID, "chunk_index", i, "error", err)
			return nil, nil, 0, fmt.Errorf("failed to generate embedding for chunk %d: %w", i, err)
		}
		if dimension == 0 {
			dimension = embeddingResult.Dimension
		}

		chunkID := buildKnowledgeChunkVectorID(knowledgeBase.ID, document.ID, chunk.ChunkNo)
		providerName := ""
		if chunk.Metadata != nil {
			if value, ok := chunk.Metadata["provider"].(string); ok {
				providerName = value
			}
		}
		sectionPath := joinKnowledgeSectionPath(directoryPath, chunk.SectionPath)
		now := time.Now()
		chunkModels = append(chunkModels, models.KnowledgeChunk{
			KnowledgeBaseID: knowledgeBase.ID,
			DocumentID:      document.ID,
			ChunkNo:         chunk.ChunkNo,
			Title:           chunk.Title,
			Content:         chunk.Content,
			ContentHash:     buildChunkContentHash(chunk.Content),
			CharCount:       chunk.CharCount,
			TokenCount:      chunk.TokenCount,
			ChunkType:       string(chunk.ChunkType),
			SectionPath:     sectionPath,
			Provider:        providerName,
			VectorID:        chunkID,
			Status:          enums.StatusOk,
			CreatedAt:       now,
			UpdatedAt:       now,
		})

		vectors = append(vectors, vectordb.Vector{
			ID:     chunkID,
			Vector: embeddingResult.Vector,
			Payload: vectordb.ChunkPayload{
				KnowledgeBaseID: knowledgeBase.ID,
				DocumentID:      document.ID,
				DocumentTitle:   document.Title,
				ChunkNo:         chunk.ChunkNo,
				ChunkType:       string(chunk.ChunkType),
				SectionPath:     sectionPath,
				Content:         chunk.Content,
				Title:           chunk.Title,
				Provider:        providerName,
			},
		})
	}

	if len(vectors) == 0 {
		return nil, nil, 0, fmt.Errorf("no vectors generated")
	}
	return vectors, chunkModels, dimension, nil
}
