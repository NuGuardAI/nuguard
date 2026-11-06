package retrieval

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestOpenAIEmbedderRejectsWrongDimension(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": []map[string]any{{"index": 0, "embedding": []float32{0.1, 0.2, 0.3}}},
		})
	}))
	t.Cleanup(server.Close)
	embedder := NewOpenAIEmbedder(OpenAIEmbedderConfig{
		BaseURL:    server.URL,
		APIKey:     "demo-key",
		Model:      "demo-embedding",
		Dimensions: 1536,
	})

	result, err := embedder.Embed(context.Background(), []string{"hello"})

	assert.ErrorIs(t, err, ErrEmbeddingDimension)
	assert.Nil(t, result)
}

func TestOpenAIEmbedderSendsAuthAndRestoresIndexOrder(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "/embeddings", r.URL.Path)
		assert.Equal(t, "Bearer demo-key", r.Header.Get("Authorization"))
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": []map[string]any{
				{"index": 1, "embedding": []float32{0, 1, 0}},
				{"index": 0, "embedding": []float32{1, 0, 0}},
			},
		})
	}))
	t.Cleanup(server.Close)
	embedder := NewOpenAIEmbedder(OpenAIEmbedderConfig{
		BaseURL:    server.URL + "/",
		APIKey:     "demo-key",
		Model:      "demo-embedding",
		Dimensions: 3,
	})

	result, err := embedder.Embed(context.Background(), []string{"first", "second"})

	require.NoError(t, err)
	assert.Equal(t, []float32{1, 0, 0}, result[0])
	assert.Equal(t, []float32{0, 1, 0}, result[1])
}
