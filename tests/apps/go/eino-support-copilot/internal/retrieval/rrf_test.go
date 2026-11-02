package retrieval

import (
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestFuseRRFRewardsAgreement(t *testing.T) {
	a, b, c := uuid.New(), uuid.New(), uuid.New()

	got := FuseRRF([]uuid.UUID{a, b}, []uuid.UUID{b, c}, 60)

	require.Len(t, got, 3)
	assert.Equal(t, b, got[0].ID)
	assert.Greater(t, got[0].Score, got[1].Score)
}

func TestFuseRRFDeduplicatesAndUsesStableTieBreak(t *testing.T) {
	a := uuid.MustParse("00000000-0000-0000-0000-000000000001")
	b := uuid.MustParse("00000000-0000-0000-0000-000000000002")

	got := FuseRRF([]uuid.UUID{b, b, a}, []uuid.UUID{a, b}, 60)

	require.Len(t, got, 2)
	assert.Equal(t, a, got[0].ID)
	assert.Equal(t, b, got[1].ID)
}
