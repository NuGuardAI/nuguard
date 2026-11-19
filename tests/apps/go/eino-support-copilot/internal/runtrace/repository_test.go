package runtrace

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/testsupport"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestRepositoryPreservesRunStepsAfterCompletion(t *testing.T) {
	ctx := context.Background()
	pool := testsupport.StartPostgres(t)
	customerID := testsupport.InsertUser(t, pool, "customer", "Ada Demo")
	var conversationID uuid.UUID
	require.NoError(t, pool.QueryRow(ctx, `
		insert into conversations (customer_id, title)
		values ($1, 'Subscription issue')
		returning id
	`, customerID).Scan(&conversationID))
	repository := NewRepository(pool)
	run, err := repository.Start(ctx, StartParams{
		ConversationID: conversationID,
		Status:         domain.RunRunning,
		Mode:           "mock",
		InputSummary:   "subscription payment succeeded but plan is free",
	})
	require.NoError(t, err)

	events := []domain.AgentEvent{
		{
			EventID: uuid.New(), ConversationID: conversationID, RunID: run.ID,
			Type: domain.EventStageStarted, Timestamp: time.Now().UTC(), Payload: json.RawMessage(`{"stage":"classify"}`),
		},
		{
			EventID: uuid.New(), ConversationID: conversationID, RunID: run.ID,
			Type: domain.EventStageCompleted, Timestamp: time.Now().UTC(), Payload: json.RawMessage(`{"stage":"classify"}`),
		},
		{
			EventID: uuid.New(), ConversationID: conversationID, RunID: run.ID,
			Type: domain.EventToolStarted, Timestamp: time.Now().UTC(),
			Payload: json.RawMessage(`{"tool":"get_payment_status","arguments":{"account_id":"acct_demo","token":"secret"}}`),
		},
	}
	for _, event := range events {
		require.NoError(t, repository.AppendStep(ctx, event))
	}
	require.NoError(t, repository.Complete(ctx, run.ID, domain.RunCompleted, "answer delivered", json.RawMessage(`{"total_tokens":0}`)))

	got, err := repository.Get(ctx, run.ID)
	require.NoError(t, err)
	assert.Equal(t, domain.RunCompleted, got.Status)
	require.Len(t, got.Steps, 3)
	assert.Equal(t, 0, got.Steps[0].Sequence)
	assert.Equal(t, events[0].EventID, got.Steps[0].ID)
	assert.Equal(t, 1, got.Steps[1].Sequence)
	assert.Equal(t, events[1].EventID, got.Steps[1].ID)
	require.NotNil(t, got.Steps[2].ToolName)
	assert.Equal(t, "get_payment_status", *got.Steps[2].ToolName)
	assert.JSONEq(t, `{"account_id":"acct_demo","token":"secret"}`, string(got.Steps[2].ToolArguments))
}
