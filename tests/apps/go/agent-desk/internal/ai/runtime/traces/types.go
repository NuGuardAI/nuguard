package traces

type RetrieverTraceItem struct {
	Query           string  `json:"query,omitempty"`
	KnowledgeBaseID int64   `json:"knowledgeBaseId,omitempty"`
	DocumentID      int64   `json:"documentId,omitempty"`
	DocumentTitle   string  `json:"documentTitle,omitempty"`
	Score           float64 `json:"score,omitempty"`
	LatencyMs       int64   `json:"latencyMs,omitempty"`
}

type RetrieverTraceSummary struct {
	TopK             int
	ScoreThreshold   float64
	ContextMaxTokens int
	MaxContextItems  int
	HitCount         int
	ContextCount     int
	EmbeddingMs      int64
	VectorSearchMs   int64
	HydrateMs        int64
	Policies         []RetrieverPolicyTraceItem
}

type RetrieverPolicyTraceItem struct {
	KnowledgeBaseID int64   `json:"knowledgeBaseId,omitempty"`
	TopK            int     `json:"topK,omitempty"`
	ScoreThreshold  float64 `json:"scoreThreshold,omitempty"`
}
