package agent

import (
	"testing"

	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestGuardRequiresReviewWhenCitationMissing(t *testing.T) {
	draft := domain.Draft{
		Intent:     "billing.subscription_activation",
		Priority:   domain.PriorityP2,
		Answer:     "Activation usually completes within the documented window.",
		Facts:      []domain.Fact{{Source: "get_account_status", Field: "status", Value: "active"}},
		Confidence: .95,
	}

	got, err := NewGuard(.70).Verify(draft, EvidenceState{FactCount: 1})

	require.NoError(t, err)
	assert.True(t, got.RequiresReview)
	assert.Contains(t, got.ReviewReasons, "MISSING_CITATION")
	assert.Contains(t, got.Answer, "人工确认")
}

func TestGuardAppliesRulesInStableOrder(t *testing.T) {
	draft := domain.Draft{
		Intent:     "billing.subscription_activation",
		Priority:   domain.PriorityP1,
		Answer:     "A generated answer.",
		Confidence: .60,
	}
	evidence := EvidenceState{ToolFailures: []ToolFailure{{Tool: "get_payment_status", Code: "TEMPORARY_UNAVAILABLE", Retryable: true}}}

	got, err := NewGuard(.70).Verify(draft, evidence)

	require.NoError(t, err)
	assert.Equal(t, []string{
		"HIGH_PRIORITY",
		"LOW_CONFIDENCE",
		"MISSING_CITATION",
		"MISSING_FACT",
		"TOOL_PARTIAL_FAILURE",
	}, got.ReviewReasons)
	assert.Empty(t, draft.ReviewReasons)
	assert.Len(t, evidence.ToolFailures, 1)
}

func TestGuardAllowsWellSupportedP2Draft(t *testing.T) {
	draft := domain.Draft{
		Intent:     "account.general",
		Priority:   domain.PriorityP2,
		Answer:     "The generated account is active.",
		Citations:  []domain.Citation{{Title: "Account guide"}},
		Facts:      []domain.Fact{{Source: "get_account_status", Field: "account_status", Value: "active"}},
		Confidence: .90,
	}

	got, err := NewGuard(.70).Verify(draft, EvidenceState{CitationCount: 1, FactCount: 1})

	require.NoError(t, err)
	assert.False(t, got.RequiresReview)
	assert.Empty(t, got.ReviewReasons)
}
