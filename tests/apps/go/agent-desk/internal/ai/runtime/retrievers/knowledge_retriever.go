package retrievers

import (
	"context"
	"strings"

	"agent-desk/internal/ai/rag"
	"agent-desk/internal/ai/runtime/traces"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"

	"github.com/mlogclub/simple/sqls"
)

const defaultRuntimeKnowledgeContextTokens = 4000
const defaultRuntimeKnowledgeTopK = 8
const defaultRuntimeKnowledgeScoreThreshold = 0.3
const defaultRuntimeKnowledgeMaxContextItems = 5

type KnowledgeRetriever struct {
	AIAgent          models.AIAgent
	knowledgeBaseIDs []int64
}

type KnowledgeRetrieveOptions struct {
	ContextMaxTokens int
	MaxContextItems  int
	TopK             int
	ScoreThreshold   float64
	QueryPreview     string
}

type KnowledgeBaseRetrievePolicy struct {
	KnowledgeBaseID int64
	TopK            int
	ScoreThreshold  float64
}

type KnowledgeRetrieveResult struct {
	KnowledgeBaseIDs []int64
	Query            string
	Options          KnowledgeRetrieveOptions
	Hits             []rag.RetrieveResult
	ContextResults   []rag.RetrieveResult
	ContextText      string
	TopScore         float64
	AnswerMode       enums.KnowledgeAnswerMode
	Trace            *rag.RetrieveTrace
	TraceItems       []traces.RetrieverTraceItem
	TraceSummary     traces.RetrieverTraceSummary
	Policies         []KnowledgeBaseRetrievePolicy
}

func NewKnowledgeRetriever(aiAgent models.AIAgent, knowledgeBaseIDs []int64) *KnowledgeRetriever {
	return &KnowledgeRetriever{
		AIAgent:          aiAgent,
		knowledgeBaseIDs: append([]int64(nil), knowledgeBaseIDs...),
	}
}

func DefaultKnowledgeRetrieveOptions() KnowledgeRetrieveOptions {
	return KnowledgeRetrieveOptions{
		ContextMaxTokens: defaultRuntimeKnowledgeContextTokens,
		MaxContextItems:  defaultRuntimeKnowledgeMaxContextItems,
	}
}

func (r *KnowledgeRetriever) ConfiguredKnowledgeBaseIDs() []int64 {
	return append([]int64(nil), r.knowledgeBaseIDs...)
}

func (r *KnowledgeRetriever) Retrieve(ctx context.Context, query string) ([]rag.RetrieveResult, *rag.RetrieveTrace, error) {
	return r.RetrieveByOptions(ctx, DefaultKnowledgeRetrieveOptions(), query)
}

func (r *KnowledgeRetriever) RetrieveByOptions(ctx context.Context, opts KnowledgeRetrieveOptions, query string) ([]rag.RetrieveResult, *rag.RetrieveTrace, error) {
	ids := r.ConfiguredKnowledgeBaseIDs()
	return rag.Retrieve.RetrieveWithTrace(ctx, rag.RetrieveRequest{
		Query:            query,
		KnowledgeBaseIDs: ids,
		TopK:             opts.TopK,
		ScoreThreshold:   opts.ScoreThreshold,
	})
}

func (r *KnowledgeRetriever) RetrieveContext(ctx context.Context, query string) (*KnowledgeRetrieveResult, error) {
	return r.RetrieveContextByOptions(ctx, DefaultKnowledgeRetrieveOptions(), query)
}

func (r *KnowledgeRetriever) RetrieveContextByOptions(ctx context.Context, opts KnowledgeRetrieveOptions, query string) (*KnowledgeRetrieveResult, error) {
	query = strings.TrimSpace(query)
	knowledgeBaseIDs := r.ConfiguredKnowledgeBaseIDs()
	policies := r.resolvePolicies(knowledgeBaseIDs, opts)
	contextMaxTokens := opts.ContextMaxTokens
	if contextMaxTokens <= 0 {
		contextMaxTokens = defaultRuntimeKnowledgeContextTokens
	}
	maxContextItems := opts.MaxContextItems
	if maxContextItems <= 0 {
		maxContextItems = defaultRuntimeKnowledgeMaxContextItems
	}
	queryPreview := strings.TrimSpace(opts.QueryPreview)
	if queryPreview == "" {
		queryPreview = query
	}
	ret := &KnowledgeRetrieveResult{
		KnowledgeBaseIDs: append([]int64(nil), knowledgeBaseIDs...),
		Query:            query,
		Options: KnowledgeRetrieveOptions{
			ContextMaxTokens: contextMaxTokens,
			MaxContextItems:  maxContextItems,
			TopK:             opts.TopK,
			ScoreThreshold:   opts.ScoreThreshold,
			QueryPreview:     queryPreview,
		},
		Policies: append([]KnowledgeBaseRetrievePolicy(nil), policies...),
	}
	if query == "" || len(knowledgeBaseIDs) == 0 {
		return ret, nil
	}
	results, trace, err := r.RetrieveByOptions(ctx, opts, query)
	if err != nil {
		return nil, err
	}
	ret.Hits = append([]rag.RetrieveResult(nil), results...)
	ret.Trace = trace
	ret.ContextResults = rag.Retrieve.SelectContextResults(results, contextMaxTokens)
	ret.ContextResults = limitContextResults(ret.ContextResults, maxContextItems)
	ret.ContextText = strings.TrimSpace(buildContextText(ret.ContextResults))
	ret.TopScore = resolveTopScore(results)
	ret.AnswerMode = resolveRuntimeAnswerMode(knowledgeBaseIDs, results)
	ret.TraceItems = buildRetrieverTraceItems(queryPreview, results, trace)
	ret.TraceSummary = buildRetrieverTraceSummary(ret.Options, ret.Policies, ret.ContextResults, results, trace)
	return ret, nil
}

func limitContextResults(results []rag.RetrieveResult, maxItems int) []rag.RetrieveResult {
	if len(results) == 0 {
		return nil
	}
	if maxItems <= 0 || len(results) <= maxItems {
		return append([]rag.RetrieveResult(nil), results...)
	}
	return append([]rag.RetrieveResult(nil), results[:maxItems]...)
}

func buildContextText(results []rag.RetrieveResult) string {
	if len(results) == 0 {
		return ""
	}
	return strings.TrimSpace(rag.Retrieve.BuildContext(context.Background(), results, 1<<30))
}

func resolveTopScore(results []rag.RetrieveResult) float64 {
	if len(results) == 0 {
		return 0
	}
	return float64(results[0].Score)
}

func (r *KnowledgeRetriever) resolvePolicies(knowledgeBaseIDs []int64, opts KnowledgeRetrieveOptions) []KnowledgeBaseRetrievePolicy {
	if len(knowledgeBaseIDs) == 0 {
		return nil
	}
	knowledgeBases := loadRuntimeKnowledgeBases(knowledgeBaseIDs)
	ret := make([]KnowledgeBaseRetrievePolicy, 0, len(knowledgeBaseIDs))
	for _, knowledgeBaseID := range knowledgeBaseIDs {
		policy := KnowledgeBaseRetrievePolicy{
			KnowledgeBaseID: knowledgeBaseID,
			TopK:            defaultRuntimeKnowledgeTopK,
			ScoreThreshold:  defaultRuntimeKnowledgeScoreThreshold,
		}
		if knowledgeBase, ok := knowledgeBases[knowledgeBaseID]; ok {
			if knowledgeBase.DefaultTopK > 0 {
				policy.TopK = knowledgeBase.DefaultTopK
			}
			if knowledgeBase.DefaultScoreThreshold > 0 {
				policy.ScoreThreshold = knowledgeBase.DefaultScoreThreshold
			}
		}
		if opts.TopK > 0 {
			policy.TopK = opts.TopK
		}
		if opts.ScoreThreshold > 0 {
			policy.ScoreThreshold = opts.ScoreThreshold
		}
		ret = append(ret, policy)
	}
	return ret
}

func resolveRuntimeAnswerMode(knowledgeBaseIDs []int64, results []rag.RetrieveResult) enums.KnowledgeAnswerMode {
	knowledgeBases := loadRuntimeKnowledgeBases(knowledgeBaseIDs)
	if len(knowledgeBases) == 0 {
		return enums.KnowledgeAnswerModeStrict
	}
	if len(results) > 0 {
		if knowledgeBase, ok := knowledgeBases[results[0].KnowledgeBaseID]; ok {
			return normalizeRuntimeAnswerMode(knowledgeBase)
		}
	}
	for _, knowledgeBaseID := range knowledgeBaseIDs {
		if knowledgeBase, ok := knowledgeBases[knowledgeBaseID]; ok {
			return normalizeRuntimeAnswerMode(knowledgeBase)
		}
	}
	return enums.KnowledgeAnswerModeStrict
}

func normalizeRuntimeAnswerMode(knowledgeBase models.KnowledgeBase) enums.KnowledgeAnswerMode {
	answerMode := enums.KnowledgeAnswerMode(knowledgeBase.AnswerMode)
	if answerMode == 0 {
		answerMode = enums.KnowledgeAnswerModeStrict
	}
	return answerMode
}

func loadRuntimeKnowledgeBases(ids []int64) map[int64]models.KnowledgeBase {
	if len(ids) == 0 {
		return nil
	}
	items := repositories.KnowledgeBaseRepository.Find(sqls.DB(), sqls.NewCnd().In("id", ids))
	if len(items) == 0 {
		return nil
	}
	ret := make(map[int64]models.KnowledgeBase, len(items))
	for _, item := range items {
		if item.Status != enums.StatusOk {
			continue
		}
		ret[item.ID] = item
	}
	return ret
}

func buildRetrieverTraceItems(queryPreview string, results []rag.RetrieveResult, trace *rag.RetrieveTrace) []traces.RetrieverTraceItem {
	if len(results) == 0 {
		return nil
	}
	latencyMs := int64(0)
	if trace != nil {
		latencyMs = trace.EmbeddingMs + trace.VectorSearchMs + trace.HydrateMs
	}
	ret := make([]traces.RetrieverTraceItem, 0, len(results))
	for _, item := range results {
		ret = append(ret, traces.RetrieverTraceItem{
			Query:           queryPreview,
			KnowledgeBaseID: item.KnowledgeBaseID,
			DocumentID:      item.DocumentID,
			DocumentTitle:   item.DocumentTitle,
			Score:           float64(item.Score),
			LatencyMs:       latencyMs,
		})
	}
	return ret
}

func buildRetrieverTraceSummary(opts KnowledgeRetrieveOptions, policies []KnowledgeBaseRetrievePolicy, contextResults []rag.RetrieveResult, results []rag.RetrieveResult, trace *rag.RetrieveTrace) traces.RetrieverTraceSummary {
	ret := traces.RetrieverTraceSummary{
		TopK:             opts.TopK,
		ScoreThreshold:   opts.ScoreThreshold,
		ContextMaxTokens: opts.ContextMaxTokens,
		MaxContextItems:  opts.MaxContextItems,
		HitCount:         len(results),
		ContextCount:     len(contextResults),
		Policies:         buildRetrieverPolicyTraceItems(policies),
	}
	if ret.TopK <= 0 && len(policies) == 1 {
		ret.TopK = policies[0].TopK
	}
	if ret.ScoreThreshold <= 0 && len(policies) == 1 {
		ret.ScoreThreshold = policies[0].ScoreThreshold
	}
	if trace != nil {
		ret.EmbeddingMs = trace.EmbeddingMs
		ret.VectorSearchMs = trace.VectorSearchMs
		ret.HydrateMs = trace.HydrateMs
	}
	return ret
}

func buildRetrieverPolicyTraceItems(policies []KnowledgeBaseRetrievePolicy) []traces.RetrieverPolicyTraceItem {
	if len(policies) == 0 {
		return nil
	}
	ret := make([]traces.RetrieverPolicyTraceItem, 0, len(policies))
	for _, item := range policies {
		ret = append(ret, traces.RetrieverPolicyTraceItem{
			KnowledgeBaseID: item.KnowledgeBaseID,
			TopK:            item.TopK,
			ScoreThreshold:  item.ScoreThreshold,
		})
	}
	return ret
}
