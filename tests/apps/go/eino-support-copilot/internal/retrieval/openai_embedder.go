package retrieval

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

var ErrEmbeddingProvider = errors.New("embedding provider error")

type OpenAIEmbedderConfig struct {
	BaseURL    string
	APIKey     string
	Model      string
	Dimensions int
	HTTPClient *http.Client
}

type OpenAIEmbedder struct {
	baseURL    string
	apiKey     string
	model      string
	dimensions int
	client     *http.Client
}

func NewOpenAIEmbedder(config OpenAIEmbedderConfig) *OpenAIEmbedder {
	client := config.HTTPClient
	if client == nil {
		client = &http.Client{Timeout: 15 * time.Second}
	}
	return &OpenAIEmbedder{
		baseURL:    strings.TrimRight(config.BaseURL, "/"),
		apiKey:     config.APIKey,
		model:      config.Model,
		dimensions: config.Dimensions,
		client:     client,
	}
}

func (e *OpenAIEmbedder) Embed(ctx context.Context, texts []string) ([][]float32, error) {
	if e.dimensions <= 0 {
		return nil, ErrEmbeddingDimension
	}
	if len(texts) == 0 {
		return [][]float32{}, nil
	}
	body, err := json.Marshal(map[string]any{"model": e.model, "input": texts})
	if err != nil {
		return nil, fmt.Errorf("%w: encode request", ErrEmbeddingProvider)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, e.baseURL+"/embeddings", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("%w: create request", ErrEmbeddingProvider)
	}
	request.Header.Set("Content-Type", "application/json")
	if e.apiKey != "" {
		request.Header.Set("Authorization", "Bearer "+e.apiKey)
	}

	response, err := e.client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("%w: request failed", ErrEmbeddingProvider)
	}
	defer response.Body.Close()
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		return nil, fmt.Errorf("%w: unexpected HTTP status %d", ErrEmbeddingProvider, response.StatusCode)
	}

	var payload struct {
		Data []struct {
			Index     int       `json:"index"`
			Embedding []float32 `json:"embedding"`
		} `json:"data"`
	}
	decoder := json.NewDecoder(io.LimitReader(response.Body, 32<<20))
	if err := decoder.Decode(&payload); err != nil {
		return nil, fmt.Errorf("%w: invalid JSON response", ErrEmbeddingProvider)
	}
	if len(payload.Data) != len(texts) {
		return nil, fmt.Errorf("%w: expected %d vectors, got %d", ErrEmbeddingProvider, len(texts), len(payload.Data))
	}

	results := make([][]float32, len(texts))
	seen := make([]bool, len(texts))
	for _, item := range payload.Data {
		if len(item.Embedding) != e.dimensions {
			return nil, fmt.Errorf("%w: expected %d, got %d", ErrEmbeddingDimension, e.dimensions, len(item.Embedding))
		}
		if item.Index < 0 || item.Index >= len(results) || seen[item.Index] {
			return nil, fmt.Errorf("%w: invalid vector index", ErrEmbeddingProvider)
		}
		seen[item.Index] = true
		results[item.Index] = item.Embedding
	}
	return results, nil
}
