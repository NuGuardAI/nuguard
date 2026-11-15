package httpapi

// NuGuard fixture: this file is not part of upstream.
//
// The upstream customer flow is two calls: POST /api/v1/conversations, then
// POST /api/v1/conversations/{id}/messages:stream, which answers as Server-Sent Events.
// POST /chat {message, session_id?} does both for the customer identified by the bearer token
// (same requireDemoCustomer check as upstream routes), keeps one conversation per (caller, session_id),
// runs the existing conversation.Service.Send flow and returns the final draft answer as JSON.

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"sync"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
)

type chatSessionKey struct {
	customerID uuid.UUID
	sessionID  string
}

// (caller, client session id) -> conversation id. In memory: a restart starts fresh sessions,
// which matches the reset-on-restart database.
var chatSessions sync.Map

type chatToolCall struct {
	Tool      string          `json:"tool"`
	Arguments json.RawMessage `json:"arguments,omitempty"`
}

// collectingSink keeps the events the SSE endpoint would have streamed to the customer.
type collectingSink struct {
	mu        sync.Mutex
	toolCalls []chatToolCall
}

func (s *collectingSink) Emit(_ context.Context, event domain.AgentEvent) error {
	if event.Type != domain.EventToolStarted {
		return nil
	}
	var call chatToolCall
	if err := json.Unmarshal(event.Payload, &call); err != nil {
		return nil
	}
	s.mu.Lock()
	s.toolCalls = append(s.toolCalls, call)
	s.mu.Unlock()
	return nil
}

func chatAdapter(service ConversationService, identity IdentityResolver) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		customerID, ok := requireDemoCustomer(w, r, identity)
		if !ok {
			return
		}
		if service == nil {
			WriteError(w, r, http.StatusServiceUnavailable, "CONVERSATION_UNAVAILABLE", "conversation service is unavailable", true)
			return
		}
		var request struct {
			Message   string `json:"message"`
			SessionID string `json:"session_id"`
		}
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10)).Decode(&request); err != nil {
			WriteError(w, r, http.StatusBadRequest, "INVALID_JSON", "request body must be valid JSON", false)
			return
		}
		request.Message = strings.TrimSpace(request.Message)
		if request.Message == "" {
			WriteError(w, r, http.StatusUnprocessableEntity, "INVALID_ARGUMENT", "message is required", false)
			return
		}
		sessionID := strings.TrimSpace(request.SessionID)
		if sessionID == "" {
			sessionID = uuid.NewString()
		}

		key := chatSessionKey{customerID: customerID, sessionID: sessionID}
		var conversationID uuid.UUID
		if existing, found := chatSessions.Load(key); found {
			conversationID = existing.(uuid.UUID)
		} else {
			created, err := service.Create(r.Context(), customerID, "Chat "+sessionID)
			if err != nil {
				WriteError(w, r, http.StatusInternalServerError, "CONVERSATION_CREATE_FAILED", "unable to create conversation", true)
				return
			}
			actual, _ := chatSessions.LoadOrStore(key, created.ID)
			conversationID = actual.(uuid.UUID)
		}

		sink := &collectingSink{}
		result, err := service.Send(r.Context(), customerID, conversationID, request.Message, sink)
		if err != nil {
			if errors.Is(err, context.Canceled) {
				return
			}
			writeJSON(w, http.StatusBadGateway, map[string]any{
				"response":   "The support copilot could not complete this request.",
				"session_id": sessionID,
				"error":      "AGENT_RUN_FAILED",
				"request_id": RequestID(r.Context()),
			})
			return
		}
		toolCalls := sink.toolCalls
		if toolCalls == nil {
			toolCalls = []chatToolCall{}
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"response":        result.Draft.Answer,
			"session_id":      sessionID,
			"conversation_id": conversationID,
			"run_id":          result.Run.ID,
			"requires_review": result.Draft.RequiresReview,
			"tool_calls":      toolCalls,
		})
	}
}
