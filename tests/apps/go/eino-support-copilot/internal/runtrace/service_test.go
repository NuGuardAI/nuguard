package runtrace

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/testsupport"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestRunDetailOrdersStepsAndRedactsSensitiveArguments(t *testing.T) {
	ctx := context.Background()
	pool := testsupport.StartPostgres(t)
	customerID := testsupport.InsertUser(t, pool, "customer", "Ada Demo")
	agentID := testsupport.InsertUser(t, pool, "agent", "Lin Agent")
	conversationID := insertRunTestConversation(t, pool, customerID)
	run, err := NewRepository(pool).Start(ctx, StartParams{
		ConversationID: conversationID, Status: domain.RunRunning, Mode: "mock", InputSummary: "billing",
	})
	require.NoError(t, err)
	require.NoError(t, NewRepository(pool).Complete(ctx, run.ID, domain.RunCompleted, `{}`, json.RawMessage(`{}`)))
	for _, sequence := range []int{2, 0, 1} {
		_, err := pool.Exec(ctx, `
			insert into agent_run_steps (
				id, run_id, sequence, event_type, input_summary, tool_arguments,
				result_summary, citations, created_at
			) values ($1, $2, $3, 'tool.completed', '{}', $4, '{}', '[]', now())
		`, uuid.New(), run.ID, sequence, json.RawMessage(`{"account_id":"acct_demo","api_key":"secret","nested":{"token":"secret-token","password":"secret-password"}}`))
		require.NoError(t, err)
	}

	detail, err := NewService(pool, nil, "mock").Get(ctx, run.ID, agentID, "agent")
	require.NoError(t, err)

	require.Len(t, detail.Steps, 3)
	assert.Equal(t, []int{0, 1, 2}, []int{detail.Steps[0].Sequence, detail.Steps[1].Sequence, detail.Steps[2].Sequence})
	var arguments map[string]any
	require.NoError(t, json.Unmarshal(detail.Steps[0].ToolArguments, &arguments))
	assert.Equal(t, "acct_demo", arguments["account_id"])
	assert.Equal(t, "***", arguments["api_key"])
	nested := arguments["nested"].(map[string]any)
	assert.Equal(t, "***", nested["token"])
	assert.Equal(t, "***", nested["password"])
}

func TestReplayCreatesLinkedRunFromOriginalInputWithoutMutatingOriginal(t *testing.T) {
	ctx := context.Background()
	pool := testsupport.StartPostgres(t)
	customerID := testsupport.InsertUser(t, pool, "customer", "Ada Demo")
	agentID := testsupport.InsertUser(t, pool, "agent", "Lin Agent")
	conversationID := insertRunTestConversation(t, pool, customerID)
	var messageID uuid.UUID
	require.NoError(t, pool.QueryRow(ctx, `insert into messages (conversation_id, role, content) values ($1, 'user', $2) returning id`, conversationID, "Replay this payment question").Scan(&messageID))
	repository := NewRepository(pool)
	original, err := repository.Start(ctx, StartParams{
		ConversationID: conversationID, MessageID: &messageID, Status: domain.RunRunning,
		Mode: "mock", InputSummary: "Replay this payment question",
	})
	require.NoError(t, err)
	require.NoError(t, repository.Complete(ctx, original.ID, domain.RunCompleted, `{}`, json.RawMessage(`{}`)))

	called := make(chan ReplayInput, 1)
	replay := func(_ context.Context, input ReplayInput, _ Sink) (domain.Draft, error) {
		called <- input
		return domain.Draft{Intent: "billing", Priority: domain.PriorityP2, Answer: "Replayed answer."}, nil
	}
	service := NewService(pool, replay, "mock")

	created, err := service.Replay(ctx, original.ID, agentID, "agent")
	require.NoError(t, err)
	assert.NotEqual(t, original.ID, created.ID)
	require.NotNil(t, created.ReplayedFromRunID)
	assert.Equal(t, original.ID, *created.ReplayedFromRunID)

	select {
	case input := <-called:
		assert.Equal(t, "Replay this payment question", input.Text)
		assert.Equal(t, conversationID, input.ConversationID)
		assert.Equal(t, customerID, input.CustomerID)
	case <-time.After(3 * time.Second):
		t.Fatal("replay engine was not called")
	}
	require.Eventually(t, func() bool {
		got, getErr := repository.Get(ctx, created.ID)
		return getErr == nil && got.Status == domain.RunCompleted
	}, 3*time.Second, 20*time.Millisecond)
	unchanged, err := repository.Get(ctx, original.ID)
	require.NoError(t, err)
	assert.Equal(t, domain.RunCompleted, unchanged.Status)

	running, err := repository.Start(ctx, StartParams{ConversationID: conversationID, Status: domain.RunRunning, Mode: "mock", InputSummary: "running"})
	require.NoError(t, err)
	_, err = service.Replay(ctx, running.ID, agentID, "agent")
	assert.ErrorIs(t, err, ErrReplayNotAllowed)
}

func insertRunTestConversation(t *testing.T, pool *pgxpool.Pool, customerID uuid.UUID) uuid.UUID {
	t.Helper()
	var conversationID uuid.UUID
	require.NoError(t, pool.QueryRow(context.Background(), `insert into conversations (customer_id, title) values ($1, 'Run test') returning id`, customerID).Scan(&conversationID))
	return conversationID
}
