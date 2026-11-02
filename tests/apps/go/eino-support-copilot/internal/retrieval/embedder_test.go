package retrieval

import (
	"context"
	"math"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestMockEmbedderIsStableAnd1536Dimensional(t *testing.T) {
	embedder := NewMockEmbedder(1536)

	first, err := embedder.Embed(context.Background(), []string{"subscription active"})
	require.NoError(t, err)
	second, err := embedder.Embed(context.Background(), []string{"subscription active"})
	require.NoError(t, err)

	require.Len(t, first, 1)
	assert.Len(t, first[0], 1536)
	assert.Equal(t, first, second)
}

func TestMockEmbedderNormalizesTokensAndVectorLength(t *testing.T) {
	embedder := NewMockEmbedder(1536)

	upper, err := embedder.Embed(context.Background(), []string{" Subscription   ACTIVE "})
	require.NoError(t, err)
	lower, err := embedder.Embed(context.Background(), []string{"subscription active"})
	require.NoError(t, err)

	assert.Equal(t, upper, lower)
	var norm float64
	for _, value := range upper[0] {
		norm += float64(value * value)
	}
	assert.InDelta(t, 1, math.Sqrt(norm), 0.00001)
}
