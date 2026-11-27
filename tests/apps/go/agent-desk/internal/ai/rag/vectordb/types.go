package vectordb

import "context"

type Vector struct {
	ID      string       `json:"id"`
	Vector  []float32    `json:"vector"`
	Payload ChunkPayload `json:"payload"`
}

type SearchRequest struct {
	CollectionName string        `json:"collectionName"`
	Vector         []float32     `json:"vector"`
	TopK           int           `json:"topK"`
	ScoreThreshold float32       `json:"scoreThreshold"`
	Filter         *SearchFilter `json:"filter,omitempty"`
}

type SearchFilter struct {
	KnowledgeBaseIDs []int64 `json:"knowledgeBaseIds,omitempty"`
	DocumentIDs      []int64 `json:"documentIds,omitempty"`
}

type SearchResult struct {
	ID      string       `json:"id"`
	Score   float32      `json:"score"`
	Payload ChunkPayload `json:"payload"`
}

type CollectionInfo struct {
	Name       string `json:"name"`
	Dimension  int    `json:"dimension"`
	PointCount int    `json:"pointCount"`
	Status     string `json:"status"`
}

type Provider interface {
	CreateCollection(ctx context.Context, name string, dimension int) error
	DeleteCollection(ctx context.Context, name string) error
	GetCollection(ctx context.Context, name string) (*CollectionInfo, error)
	ListCollections(ctx context.Context) ([]string, error)

	UpsertVectors(ctx context.Context, collectionName string, vectors []Vector) error
	DeleteVectors(ctx context.Context, collectionName string, ids []string) error

	Search(ctx context.Context, req *SearchRequest) ([]SearchResult, error)
	Close() error
}
