package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/conversation"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/runtrace"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestConversationStreamReturnsSSEEvents(t *testing.T) {
	conversationID := uuid.New()
	service := &conversationServiceStub{events: []domain.AgentEvent{
		fixedStreamEvent(domain.EventRunStarted, json.RawMessage(`{"mode":"mock"}`)),
		fixedStreamEvent(domain.EventRunCompleted, json.RawMessage(`{"status":"completed"}`)),
	}}
	handler := NewServer(Dependencies{
		Mode: "mock", Conversations: service,
		Identity: &identityStub{id: uuid.New(), role: "customer"},
	})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/v1/conversations/"+conversationID.String()+"/messages:stream", bytes.NewBufferString(`{"text":"Is my account active?"}`))
	request.Header.Set("Authorization", "Bearer "+"demo-customer") // NuGuard fixture: was X-Demo-User

	handler.ServeHTTP(recorder, request)

	require.Equal(t, http.StatusOK, recorder.Code)
	assert.Equal(t, "text/event-stream", recorder.Header().Get("Content-Type"))
	assert.Contains(t, recorder.Body.String(), "event: run.started")
	assert.Contains(t, recorder.Body.String(), "event: run.completed")
	assert.Less(t, bytes.Index(recorder.Body.Bytes(), []byte("run.started")), bytes.Index(recorder.Body.Bytes(), []byte("run.completed")))
}

func TestConversationStreamValidatesTextAndAccess(t *testing.T) {
	conversationID := uuid.New()
	tests := []struct {
		name       string
		identity   IdentityResolver
		header     string
		body       string
		accessErr  error
		wantStatus int
		wantCode   string
	}{
		{name: "missing identity", identity: &identityStub{id: uuid.New(), role: "customer"}, body: `{"text":"hi"}`, wantStatus: 401, wantCode: "UNAUTHENTICATED"},
		{name: "wrong role", identity: &identityStub{id: uuid.New(), role: "agent"}, header: "demo-agent", body: `{"text":"hi"}`, wantStatus: 403, wantCode: "FORBIDDEN"},
		{name: "empty", identity: &identityStub{id: uuid.New(), role: "customer"}, header: "demo-customer", body: `{"text":"  "}`, wantStatus: 422, wantCode: "INVALID_ARGUMENT"},
		{name: "not found", identity: &identityStub{id: uuid.New(), role: "customer"}, header: "demo-customer", body: `{"text":"hi"}`, accessErr: conversation.ErrNotFound, wantStatus: 404, wantCode: "CONVERSATION_NOT_FOUND"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			service := &conversationServiceStub{accessErr: tt.accessErr}
			handler := NewServer(Dependencies{Mode: "mock", Conversations: service, Identity: tt.identity})
			recorder := httptest.NewRecorder()
			request := httptest.NewRequest(http.MethodPost, "/api/v1/conversations/"+conversationID.String()+"/messages:stream", bytes.NewBufferString(tt.body))
			if tt.header != "" {
				request.Header.Set("Authorization", "Bearer "+tt.header) // NuGuard fixture: was X-Demo-User
			}

			handler.ServeHTTP(recorder, request)

			require.Equal(t, tt.wantStatus, recorder.Code)
			var response ErrorResponse
			require.NoError(t, json.NewDecoder(recorder.Body).Decode(&response))
			assert.Equal(t, tt.wantCode, response.Code)
		})
	}
}

type conversationServiceStub struct {
	events    []domain.AgentEvent
	accessErr error
}

func (s *conversationServiceStub) Create(context.Context, uuid.UUID, string) (domain.Conversation, error) {
	return domain.Conversation{ID: uuid.New()}, nil
}

func (s *conversationServiceStub) List(context.Context, uuid.UUID) ([]domain.Conversation, error) {
	return []domain.Conversation{}, nil
}

func (s *conversationServiceStub) CanAccess(context.Context, uuid.UUID, uuid.UUID) error {
	return s.accessErr
}

func (s *conversationServiceStub) Send(_ context.Context, _, _ uuid.UUID, _ string, sink runtrace.Sink) (conversation.SendResult, error) {
	for _, event := range s.events {
		if err := sink.Emit(context.Background(), event); err != nil {
			return conversation.SendResult{}, err
		}
	}
	return conversation.SendResult{}, nil
}

type identityStub struct {
	id   uuid.UUID
	role string
	err  error
}

func (s *identityStub) Resolve(context.Context, string) (uuid.UUID, string, error) {
	return s.id, s.role, s.err
}
