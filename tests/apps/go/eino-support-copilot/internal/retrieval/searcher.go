package retrieval

import (
	"context"
	"errors"
	"strings"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
)

var ErrInvalidSearchResult = errors.New("invalid search result")

type Searcher struct {
	store    *Store
	embedder Embedder
	minScore float64
}

func NewSearcher(store *Store, embedder Embedder, minScore float64) *Searcher {
	return &Searcher{store: store, embedder: embedder, minScore: minScore}
}

func (s *Searcher) Search(ctx context.Context, query string, limit int) ([]domain.Citation, error) {
	query = strings.TrimSpace(query)
	if query == "" || limit <= 0 {
		return []domain.Citation{}, nil
	}
	if limit > 6 {
		limit = 6
	}

	keywordIDs, err := s.store.KeywordIDs(ctx, query, candidateLimit)
	if err != nil {
		return nil, err
	}
	vectors, err := s.embedder.Embed(ctx, []string{query})
	if err != nil {
		return nil, err
	}
	if len(vectors) != 1 {
		return nil, ErrInvalidSearchResult
	}
	vectorIDs, err := s.store.VectorIDs(ctx, vectors[0], candidateLimit)
	if err != nil {
		return nil, err
	}

	ranked := FuseRRF(keywordIDs, vectorIDs, 60)
	selected := make([]ScoredID, 0, limit)
	ids := make([]uuid.UUID, 0, limit)
	for _, item := range ranked {
		if item.Score < s.minScore {
			continue
		}
		selected = append(selected, item)
		ids = append(ids, item.ID)
		if len(selected) == limit {
			break
		}
	}
	if len(selected) == 0 {
		return []domain.Citation{}, nil
	}

	chunks, err := s.store.ChunksByID(ctx, ids)
	if err != nil {
		return nil, err
	}
	citations := make([]domain.Citation, 0, len(selected))
	for _, item := range selected {
		chunk, exists := chunks[item.ID]
		if !exists {
			return nil, ErrInvalidSearchResult
		}
		citations = append(citations, domain.Citation{
			ChunkID: chunk.ID,
			Title:   chunk.Title,
			Locator: chunk.Locator,
			Content: chunk.Content,
			Score:   item.Score,
		})
	}
	return citations, nil
}
