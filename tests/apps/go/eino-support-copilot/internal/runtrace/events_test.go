package runtrace

import (
	"context"
	"encoding/json"
	"errors"
	"testing"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestNewEventUsesUUIDAndUTCTimestamp(t *testing.T) {
	event := NewEvent(uuid.New(), uuid.New(), uuid.New(), domain.EventStageStarted, json.RawMessage(`{"stage":"retrieve"}`))

	assert.NotEqual(t, uuid.Nil, event.EventID)
	assert.Equal(t, "UTC", event.Timestamp.Location().String())
	assert.Equal(t, domain.EventStageStarted, event.Type)
}

func TestPersistentSinkPersistsBeforeNotifying(t *testing.T) {
	order := make([]string, 0, 2)
	appender := stepAppenderFunc(func(context.Context, domain.AgentEvent) error {
		order = append(order, "persist")
		return nil
	})
	sink := NewPersistentSink(appender, SubscriberFunc(func(context.Context, domain.AgentEvent) error {
		order = append(order, "notify")
		return nil
	}))

	err := sink.Emit(context.Background(), domain.AgentEvent{EventID: uuid.New()})

	require.NoError(t, err)
	assert.Equal(t, []string{"persist", "notify"}, order)
}

func TestPersistentSinkDoesNotNotifyWhenPersistenceFails(t *testing.T) {
	persistErr := errors.New("database unavailable")
	notified := false
	sink := NewPersistentSink(
		stepAppenderFunc(func(context.Context, domain.AgentEvent) error { return persistErr }),
		SubscriberFunc(func(context.Context, domain.AgentEvent) error {
			notified = true
			return nil
		}),
	)

	err := sink.Emit(context.Background(), domain.AgentEvent{EventID: uuid.New()})

	assert.ErrorIs(t, err, persistErr)
	assert.False(t, notified)
}

type stepAppenderFunc func(context.Context, domain.AgentEvent) error

func (f stepAppenderFunc) AppendStep(ctx context.Context, event domain.AgentEvent) error {
	return f(ctx, event)
}
