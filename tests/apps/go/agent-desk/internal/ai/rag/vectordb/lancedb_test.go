//go:build lancedb

package vectordb

import (
	"context"
	"testing"

	"agent-desk/internal/pkg/config"
)

func TestLanceDBProviderVectorLifecycle(t *testing.T) {
	ctx := context.Background()
	provider, err := NewLanceDBProvider(&config.LanceDBVectorDBConfig{Path: t.TempDir()})
	if err != nil {
		t.Fatalf("NewLanceDBProvider() error = %v", err)
	}
	defer provider.Close()

	const collectionName = "knowledge_chunks"
	if err := provider.CreateCollection(ctx, collectionName, 3); err != nil {
		t.Fatalf("CreateCollection() error = %v", err)
	}

	vectors := []Vector{
		{
			ID:     "a",
			Vector: []float32{1, 0, 0},
			Payload: ChunkPayload{
				KnowledgeBaseID: 10,
				DocumentID:      100,
				Title:           "A",
				Content:         "alpha",
			},
		},
		{
			ID:     "b",
			Vector: []float32{0, 1, 0},
			Payload: ChunkPayload{
				KnowledgeBaseID: 20,
				DocumentID:      200,
				Title:           "B",
				Content:         "beta",
			},
		},
	}
	if err := provider.UpsertVectors(ctx, collectionName, vectors); err != nil {
		t.Fatalf("UpsertVectors() error = %v", err)
	}

	info, err := provider.GetCollection(ctx, collectionName)
	if err != nil {
		t.Fatalf("GetCollection() error = %v", err)
	}
	if info.Dimension != 3 {
		t.Fatalf("CollectionInfo.Dimension = %d, want 3", info.Dimension)
	}
	if info.PointCount != 2 {
		t.Fatalf("CollectionInfo.PointCount = %d, want 2", info.PointCount)
	}

	results, err := provider.Search(ctx, &SearchRequest{
		CollectionName: collectionName,
		Vector:         []float32{1, 0, 0},
		TopK:           5,
		ScoreThreshold: 0,
		Filter: &SearchFilter{
			KnowledgeBaseIDs: []int64{10},
		},
	})
	if err != nil {
		t.Fatalf("Search() error = %v", err)
	}
	if len(results) != 1 {
		t.Fatalf("Search() returned %d results, want 1: %#v", len(results), results)
	}
	if results[0].ID != "a" {
		t.Fatalf("Search()[0].ID = %q, want %q", results[0].ID, "a")
	}
	if results[0].Payload.KnowledgeBaseID != 10 {
		t.Fatalf("Search()[0].Payload.KnowledgeBaseID = %d, want 10", results[0].Payload.KnowledgeBaseID)
	}

	if err := provider.DeleteVectors(ctx, collectionName, []string{"a"}); err != nil {
		t.Fatalf("DeleteVectors() error = %v", err)
	}
	results, err = provider.Search(ctx, &SearchRequest{
		CollectionName: collectionName,
		Vector:         []float32{1, 0, 0},
		TopK:           5,
		ScoreThreshold: 0,
		Filter: &SearchFilter{
			KnowledgeBaseIDs: []int64{10},
		},
	})
	if err != nil {
		t.Fatalf("Search() after delete error = %v", err)
	}
	if len(results) != 0 {
		t.Fatalf("Search() after delete returned %d results, want 0: %#v", len(results), results)
	}
}
