package rag

import (
	"fmt"
	"log/slog"

	"agent-desk/internal/models"
)

func newRetrieveTrace() *RetrieveTrace {
	return &RetrieveTrace{}
}

func (s *retrieve) prepareRetrievableKnowledgeBases(req RetrieveRequest, trace *RetrieveTrace) ([]models.KnowledgeBase, []int64, bool) {
	if req.Query == "" {
		return nil, nil, false
	}
	knowledgeBaseIDs := normalizeKnowledgeBaseIDs(req.KnowledgeBaseIDs)
	if len(knowledgeBaseIDs) == 0 {
		return nil, nil, false
	}

	retrievableKnowledgeBases := s.loadRetrievableKnowledgeBases(knowledgeBaseIDs)
	if len(retrievableKnowledgeBases) == 0 {
		slog.Info("Skip retrieve for non-enabled knowledge bases",
			"knowledge_base_ids", fmt.Sprint(knowledgeBaseIDs))
		return nil, knowledgeBaseIDs, false
	}
	return retrievableKnowledgeBases, knowledgeBaseIDs, true
}

func applySearchTrace(target *RetrieveTrace, source *RetrieveTrace) {
	if target == nil || source == nil {
		return
	}
	target.EmbeddingMs = source.EmbeddingMs
	target.VectorSearchMs = source.VectorSearchMs
}
