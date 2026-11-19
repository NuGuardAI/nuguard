package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/runtrace"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestWriteEventUsesEventIDTypeAndJSONData(t *testing.T) {
	var buffer bytes.Buffer
	event := fixedStreamEvent(domain.EventStageStarted, json.RawMessage(`{"stage":"retrieve"}`))

	require.NoError(t, WriteEvent(&buffer, event))

	assert.Contains(t, buffer.String(), "id: "+event.EventID.String()+"\n")
	assert.Contains(t, buffer.String(), "event: stage.started\n")
	assert.Contains(t, buffer.String(), `data: {"event_id"`)
	assert.True(t, strings.HasSuffix(buffer.String(), "\n\n"))
}

func TestStreamCancellationReachesOperationAndMarksRunCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	request := httptest.NewRequest("POST", "/api/v1/chat/stream", nil).WithContext(ctx)
	recorder := httptest.NewRecorder()
	started := make(chan struct{})
	engineCancelled := make(chan struct{})
	var status atomic.Value
	status.Store(domain.RunRunning)

	done := make(chan error, 1)
	go func() {
		done <- ServeEventStream(
			recorder,
			request,
			runtrace.SubscriberFunc(func(context.Context, domain.AgentEvent) error { return nil }),
			func(operationCtx context.Context, _ runtrace.Sink) error {
				close(started)
				<-operationCtx.Done()
				close(engineCancelled)
				return operationCtx.Err()
			},
			func(context.Context) error {
				status.Store(domain.RunCancelled)
				return nil
			},
		)
	}()

	<-started
	cancel()
	select {
	case <-engineCancelled:
	case <-time.After(time.Second):
		t.Fatal("operation did not receive request cancellation")
	}
	require.NoError(t, <-done)
	assert.Equal(t, domain.RunCancelled, status.Load())
	assert.Equal(t, "text/event-stream", recorder.Header().Get("Content-Type"))
	assert.Equal(t, "no-cache", recorder.Header().Get("Cache-Control"))
	assert.Equal(t, "no", recorder.Header().Get("X-Accel-Buffering"))
}

func fixedStreamEvent(eventType domain.EventType, payload json.RawMessage) domain.AgentEvent {
	return domain.AgentEvent{
		EventID:        uuid.MustParse("00000000-0000-0000-0000-000000000100"),
		ConversationID: uuid.MustParse("00000000-0000-0000-0000-000000000101"),
		MessageID:      uuid.MustParse("00000000-0000-0000-0000-000000000102"),
		RunID:          uuid.MustParse("00000000-0000-0000-0000-000000000103"),
		Type:           eventType,
		Timestamp:      time.Date(2026, 8, 13, 8, 0, 0, 0, time.UTC),
		Payload:        payload,
	}
}
