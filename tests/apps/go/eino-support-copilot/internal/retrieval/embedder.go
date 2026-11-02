package retrieval

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"math"
	"strings"
)

var ErrEmbeddingDimension = errors.New("embedding dimension mismatch")

type Embedder interface {
	Embed(context.Context, []string) ([][]float32, error)
}

type MockEmbedder struct {
	dimensions int
}

func NewMockEmbedder(dimensions int) *MockEmbedder {
	return &MockEmbedder{dimensions: dimensions}
}

func (e *MockEmbedder) Embed(ctx context.Context, texts []string) ([][]float32, error) {
	if e.dimensions <= 0 {
		return nil, ErrEmbeddingDimension
	}
	results := make([][]float32, len(texts))
	for index, text := range texts {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		vector := make([]float32, e.dimensions)
		for _, token := range strings.Fields(strings.ToLower(strings.TrimSpace(text))) {
			hash := sha256.Sum256([]byte(token))
			position := binary.BigEndian.Uint64(hash[:8]) % uint64(e.dimensions)
			sign := float32(1)
			if hash[8]&1 == 1 {
				sign = -1
			}
			vector[position] += sign
		}
		normalize(vector)
		results[index] = vector
	}
	return results, nil
}

func normalize(vector []float32) {
	var sum float64
	for _, value := range vector {
		sum += float64(value * value)
	}
	if sum == 0 {
		return
	}
	scale := float32(1 / math.Sqrt(sum))
	for index := range vector {
		vector[index] *= scale
	}
}
