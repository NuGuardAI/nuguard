package rag

import (
	"context"
	"fmt"
	"log/slog"
	"strings"

	"agent-desk/internal/ai/rag/vectordb"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"

	"github.com/mlogclub/simple/sqls"
)

type retrieve struct {
}

var Retrieve = &retrieve{}

func (s *retrieve) Retrieve(ctx context.Context, req RetrieveRequest) ([]RetrieveResult, error) {
	results, _, err := s.RetrieveWithTrace(ctx, req)
	return results, err
}

type RetrieveTrace struct {
	EmbeddingMs    int64
	VectorSearchMs int64
	HydrateMs      int64
}

func (s *retrieve) RetrieveWithTrace(ctx context.Context, req RetrieveRequest) ([]RetrieveResult, *RetrieveTrace, error) {
	trace := newRetrieveTrace()
	retrievableKnowledgeBases, _, ok := s.prepareRetrievableKnowledgeBases(req, trace)
	if !ok {
		return nil, trace, nil
	}

	searchResults, searchTrace, err := s.searchKnowledgeBaseVectors(ctx, req, retrievableKnowledgeBases)
	if err != nil {
		applySearchTrace(trace, searchTrace)
		return nil, trace, err
	}
	applySearchTrace(trace, searchTrace)

	if len(searchResults) == 0 {
		return nil, trace, nil
	}
	results, hydrateMs := s.hydrateRetrieveResults(searchResults)
	trace.HydrateMs = hydrateMs

	return results, trace, nil
}

func extractChunkType(payload vectordb.ChunkPayload) string {
	if payload.ChunkType != "" {
		return payload.ChunkType
	}
	return string(enums.KnowledgeChunkTypeText)
}

func (s *retrieve) logEmptySearchDiagnostics(ctx context.Context, provider vectordb.Provider, collectionName string, vector []float32, topK int, scoreThreshold float32, knowledgeBaseIDs []int64, req RetrieveRequest) {
	rawResults, err := provider.Search(ctx, &vectordb.SearchRequest{
		CollectionName: collectionName,
		Vector:         vector,
		TopK:           topK,
		ScoreThreshold: 0,
		Filter: &vectordb.SearchFilter{
			KnowledgeBaseIDs: knowledgeBaseIDs,
		},
	})
	if err != nil {
		slog.Warn("Knowledge retrieve diagnostics failed",
			"knowledge_base_ids", fmt.Sprint(knowledgeBaseIDs),
			"collection", collectionName,
			"query", truncateForLog(req.Query, 80),
			"score_threshold", scoreThreshold,
			"error", err)
		return
	}
	if len(rawResults) == 0 {
		slog.Info("Knowledge retrieve returned no candidates even without threshold",
			"knowledge_base_ids", fmt.Sprint(knowledgeBaseIDs),
			"collection", collectionName,
			"query", truncateForLog(req.Query, 80),
			"score_threshold", scoreThreshold)
		return
	}

	candidates := make([]string, 0, len(rawResults))
	for _, item := range rawResults {
		candidates = append(candidates, fmt.Sprintf("%s:%.4f", item.ID, item.Score))
	}

	slog.Info("Knowledge retrieve filtered all candidates by score threshold",
		"knowledge_base_ids", fmt.Sprint(knowledgeBaseIDs),
		"collection", collectionName,
		"query", truncateForLog(req.Query, 80),
		"score_threshold", scoreThreshold,
		"top_candidates", strings.Join(candidates, ","))
}

func truncateForLog(text string, limit int) string {
	if limit <= 0 {
		return ""
	}
	runes := []rune(strings.TrimSpace(text))
	if len(runes) <= limit {
		return string(runes)
	}
	return string(runes[:limit]) + "..."
}

func (s *retrieve) RetrieveWithRerank(ctx context.Context, req RetrieveRequest, rerankLimit int) ([]RetrieveResult, error) {
	results, err := s.Retrieve(ctx, req)
	if err != nil {
		return nil, err
	}

	if len(results) <= rerankLimit {
		return results, nil
	}

	rerankedResults, err := s.rerank(ctx, req.Query, results, rerankLimit)
	if err != nil {
		slog.Warn("Rerank failed, returning original results", "error", err)
		if len(results) > rerankLimit {
			return results[:rerankLimit], nil
		}
		return results, nil
	}

	return rerankedResults, nil
}

func (s *retrieve) rerank(ctx context.Context, query string, results []RetrieveResult, limit int) ([]RetrieveResult, error) {
	return Rerank.RerankResults(ctx, query, results, limit)
}

func (s *retrieve) GetKnowledgeBaseStats(ctx context.Context, knowledgeBaseID int64) (*KnowledgeBaseStats, error) {
	knowledgeBase := repositories.KnowledgeBaseRepository.Get(sqls.DB(), knowledgeBaseID)
	if knowledgeBase == nil {
		return nil, fmt.Errorf("knowledge base not found")
	}

	documentCount := repositories.KnowledgeDocumentRepository.CountByKnowledgeBaseID(sqls.DB(), knowledgeBaseID)
	chunkCount := repositories.KnowledgeChunkRepository.CountByKnowledgeBaseID(sqls.DB(), knowledgeBaseID)

	publishedCount := repositories.KnowledgeDocumentRepository.Count(sqls.DB(), sqls.NewCnd().
		Eq("knowledge_base_id", knowledgeBaseID).
		Eq("status", enums.StatusOk))

	return &KnowledgeBaseStats{
		KnowledgeBaseID: knowledgeBaseID,
		DocumentCount:   documentCount,
		PublishedCount:  publishedCount,
		ChunkCount:      chunkCount,
		VectorCount:     int(chunkCount),
	}, nil
}

func normalizeKnowledgeBaseIDs(ids []int64) []int64 {
	if len(ids) == 0 {
		return nil
	}
	seen := make(map[int64]struct{}, len(ids))
	normalized := make([]int64, 0, len(ids))
	for _, id := range ids {
		if id <= 0 {
			continue
		}
		if _, ok := seen[id]; ok {
			continue
		}
		seen[id] = struct{}{}
		normalized = append(normalized, id)
	}
	return normalized
}

func resolveKnowledgeBaseSearchOptions(req RetrieveRequest, knowledgeBase *models.KnowledgeBase) (int, float32) {
	topK := req.TopK
	if topK <= 0 && knowledgeBase != nil && knowledgeBase.DefaultTopK > 0 {
		topK = knowledgeBase.DefaultTopK
	}
	if topK <= 0 {
		topK = 8
	}

	scoreThreshold := float32(req.ScoreThreshold)
	if scoreThreshold <= 0 && knowledgeBase != nil && knowledgeBase.DefaultScoreThreshold > 0 {
		scoreThreshold = float32(knowledgeBase.DefaultScoreThreshold)
	}
	if scoreThreshold <= 0 {
		scoreThreshold = 0.3
	}
	return topK, scoreThreshold
}

func (s *retrieve) loadRetrievableKnowledgeBases(ids []int64) []models.KnowledgeBase {
	if len(ids) == 0 {
		return nil
	}
	items := repositories.KnowledgeBaseRepository.Find(sqls.DB(), sqls.NewCnd().In("id", ids))
	if len(items) == 0 {
		return nil
	}
	allowed := make(map[int64]models.KnowledgeBase, len(items))
	for _, item := range items {
		if item.Status == enums.StatusOk {
			allowed[item.ID] = item
		}
	}
	filtered := make([]models.KnowledgeBase, 0, len(ids))
	for _, id := range ids {
		if item, ok := allowed[id]; ok {
			filtered = append(filtered, item)
		}
	}
	return filtered
}

type KnowledgeBaseStats struct {
	KnowledgeBaseID int64 `json:"knowledgeBaseId"`
	DocumentCount   int64 `json:"documentCount"`
	PublishedCount  int64 `json:"publishedCount"`
	ChunkCount      int64 `json:"chunkCount"`
	VectorCount     int   `json:"vectorCount"`
}
