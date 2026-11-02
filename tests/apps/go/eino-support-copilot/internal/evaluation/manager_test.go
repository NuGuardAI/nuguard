package evaluation

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/testsupport"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestManagerPersistsEvaluationProgressAndResults(t *testing.T) {
	ctx := context.Background()
	pool := testsupport.StartPostgres(t)
	dataset := Dataset{Name: "manager-test", Version: "1", Cases: []Case{{
		ID: "payment-01", Input: "Payment?", ExpectedIntent: "billing.payment_status", ExpectedPriority: "P2",
		ExpectedTools:     []ExpectedTool{{Name: "get_payment_status", Arguments: map[string]string{"account_id": "acct_demo_pro"}}},
		RequiredCitations: []ExpectedCitation{{Title: "Payment guide", Locator: "Status"}},
	}}}
	manager := NewManager(pool, NewRunner(ExpectedExecutor{}, RunConfig{CommitSHA: "test", Mode: "mock"}), dataset)

	queued, err := manager.Start(ctx)
	require.NoError(t, err)
	assert.Equal(t, "queued", queued.Status)
	runID, err := uuid.Parse(queued.ID)
	require.NoError(t, err)

	var completed RunResult
	require.Eventually(t, func() bool {
		completed, err = manager.Get(ctx, runID)
		return err == nil && completed.Status == "completed"
	}, 3*time.Second, 20*time.Millisecond)
	assert.Equal(t, 1, completed.Completed)
	require.Len(t, completed.Results, 1)
	assert.True(t, completed.Results[0].Passed)
	assert.Equal(t, int64(1), completed.Metrics["intent_accuracy"].Denominator)
}
