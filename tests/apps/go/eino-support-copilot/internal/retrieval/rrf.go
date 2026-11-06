package retrieval

import (
	"bytes"
	"sort"

	"github.com/google/uuid"
)

type ScoredID struct {
	ID    uuid.UUID
	Score float64
}

func FuseRRF(keywordIDs, vectorIDs []uuid.UUID, k int) []ScoredID {
	if k < 1 {
		k = 60
	}
	scores := make(map[uuid.UUID]float64, len(keywordIDs)+len(vectorIDs))
	addRanking := func(ids []uuid.UUID) {
		seen := make(map[uuid.UUID]struct{}, len(ids))
		rank := 0
		for _, id := range ids {
			if id == uuid.Nil {
				continue
			}
			if _, exists := seen[id]; exists {
				continue
			}
			seen[id] = struct{}{}
			rank++
			scores[id] += 1 / float64(k+rank)
		}
	}
	addRanking(keywordIDs)
	addRanking(vectorIDs)

	result := make([]ScoredID, 0, len(scores))
	for id, score := range scores {
		result = append(result, ScoredID{ID: id, Score: score})
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].Score != result[j].Score {
			return result[i].Score > result[j].Score
		}
		return bytes.Compare(result[i].ID[:], result[j].ID[:]) < 0
	})
	return result
}
