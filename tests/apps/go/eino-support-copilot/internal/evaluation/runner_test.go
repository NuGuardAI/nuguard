package evaluation

import (
	"context"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestRunnerProducesRepeatableScoresMetricsAndOrder(t *testing.T) {
	dataset := Dataset{Name: "support-golden", Version: "test", Cases: []Case{
		{ID: "case-b", Input: "Payment?", ExpectedIntent: "billing.payment_status", ExpectedPriority: "P2", ExpectedTools: []ExpectedTool{{Name: "get_payment_status", Arguments: map[string]string{"account_id": "acct_demo_pro"}}}, RequiredCitations: []ExpectedCitation{{Title: "Payment guide", Locator: "Status"}}},
		{ID: "case-a", Input: "Account?", ExpectedIntent: "account.general", ExpectedPriority: "P2", ExpectedTools: []ExpectedTool{{Name: "get_account_status", Arguments: map[string]string{"account_id": "acct_demo_pro"}}}, RequiredCitations: []ExpectedCitation{{Title: "Account guide", Locator: "State"}}},
	}}
	runner := NewRunner(ExpectedExecutor{}, RunConfig{CommitSHA: "abc123", Mode: "mock"})

	first, err := runner.Run(context.Background(), dataset)
	require.NoError(t, err)
	second, err := runner.Run(context.Background(), dataset)
	require.NoError(t, err)

	assert.Equal(t, first.DatasetHash, second.DatasetHash)
	assert.Equal(t, first.Metrics, second.Metrics)
	assert.Equal(t, first.Results, second.Results)
	assert.Equal(t, []string{"case-b", "case-a"}, []string{first.Results[0].CaseID, first.Results[1].CaseID})
	assert.Equal(t, "abc123", first.Config.CommitSHA)
	assert.Equal(t, "mock", first.Config.Mode)
	assert.False(t, first.Config.Timestamp.IsZero())
	assert.Equal(t, int64(2), first.Metrics["intent_accuracy"].Denominator)
}

func TestGoldenDatasetContainsTwentyFourCasesAcrossSixCategories(t *testing.T) {
	dataset, err := LoadJSONL("../../seed/eval/support-golden.jsonl")
	require.NoError(t, err)
	require.Len(t, dataset.Cases, 24)
	categories := map[string]int{}
	for _, testCase := range dataset.Cases {
		category, _, _ := strings.Cut(testCase.ID, "-")
		categories[category]++
	}
	assert.Equal(t, map[string]int{"subscription": 4, "payment": 4, "members": 4, "invoice": 4, "service": 4, "unsupported": 4}, categories)
}
