package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/conversation"
	"github.com/guiyangyuan/eino-support-copilot/internal/runtrace"
)

func createConversation(service ConversationService, identity IdentityResolver) http.HandlerFunc {
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
			Title string `json:"title"`
		}
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10)).Decode(&request); err != nil {
			WriteError(w, r, http.StatusBadRequest, "INVALID_JSON", "request body must be valid JSON", false)
			return
		}
		created, err := service.Create(r.Context(), customerID, request.Title)
		if err != nil {
			WriteError(w, r, http.StatusInternalServerError, "CONVERSATION_CREATE_FAILED", "unable to create conversation", true)
			return
		}
		writeJSON(w, http.StatusCreated, created)
	}
}

func listConversations(service ConversationService, identity IdentityResolver) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		customerID, ok := requireDemoCustomer(w, r, identity)
		if !ok {
			return
		}
		if service == nil {
			WriteError(w, r, http.StatusServiceUnavailable, "CONVERSATION_UNAVAILABLE", "conversation service is unavailable", true)
			return
		}
		items, err := service.List(r.Context(), customerID)
		if err != nil {
			WriteError(w, r, http.StatusInternalServerError, "CONVERSATION_LIST_FAILED", "unable to list conversations", true)
			return
		}
		writeJSON(w, http.StatusOK, items)
	}
}

func streamConversationMessage(service ConversationService, identity IdentityResolver) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		customerID, ok := requireDemoCustomer(w, r, identity)
		if !ok {
			return
		}
		if service == nil {
			WriteError(w, r, http.StatusServiceUnavailable, "CONVERSATION_UNAVAILABLE", "conversation service is unavailable", true)
			return
		}
		conversationID, err := uuid.Parse(r.PathValue("id"))
		if err != nil {
			WriteError(w, r, http.StatusNotFound, "CONVERSATION_NOT_FOUND", "conversation not found", false)
			return
		}
		var request struct {
			Text string `json:"text"`
		}
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10)).Decode(&request); err != nil {
			WriteError(w, r, http.StatusBadRequest, "INVALID_JSON", "request body must be valid JSON", false)
			return
		}
		request.Text = strings.TrimSpace(request.Text)
		if request.Text == "" {
			WriteError(w, r, http.StatusUnprocessableEntity, "INVALID_ARGUMENT", "text is required", false)
			return
		}
		if err := service.CanAccess(r.Context(), customerID, conversationID); err != nil {
			writeConversationAccessError(w, r, err)
			return
		}
		err = ServeEventStream(w, r, nil, func(ctx context.Context, sink runtrace.Sink) error {
			_, sendErr := service.Send(ctx, customerID, conversationID, request.Text, sink)
			return sendErr
		}, nil)
		if err != nil && !errors.Is(err, context.Canceled) {
			return
		}
	}
}

func requireDemoCustomer(w http.ResponseWriter, r *http.Request, identity IdentityResolver) (uuid.UUID, bool) {
	return requireDemoRole(w, r, identity, "customer")
}

func requireDemoRole(w http.ResponseWriter, r *http.Request, identity IdentityResolver, requiredRole string) (uuid.UUID, bool) {
	id, role, ok := requireDemoIdentity(w, r, identity)
	if !ok {
		return uuid.Nil, false
	}
	if role != requiredRole {
		WriteError(w, r, http.StatusForbidden, "FORBIDDEN", requiredRole+" role is required", false)
		return uuid.Nil, false
	}
	return id, true
}

func requireDemoIdentity(w http.ResponseWriter, r *http.Request, identity IdentityResolver) (uuid.UUID, string, bool) {
	// NuGuard fixture: upstream read an unauthenticated X-Demo-User: <uuid> header here. The caller
	// is now identified only by "Authorization: Bearer <jwt>" from POST /auth/token (see auth.go).
	authorization := strings.TrimSpace(r.Header.Get("Authorization"))
	raw := ""
	if len(authorization) > len("Bearer ") && strings.EqualFold(authorization[:len("Bearer ")], "Bearer ") {
		raw = strings.TrimSpace(authorization[len("Bearer "):])
	}
	if raw == "" || identity == nil {
		WriteError(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "Authorization: Bearer token is required", false)
		return uuid.Nil, "", false
	}
	id, role, err := identity.Resolve(r.Context(), raw)
	if err != nil {
		WriteError(w, r, http.StatusUnauthorized, "UNAUTHENTICATED", "bearer token is invalid or expired", false)
		return uuid.Nil, "", false
	}
	return id, role, true
}

func writeConversationAccessError(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, conversation.ErrNotFound):
		WriteError(w, r, http.StatusNotFound, "CONVERSATION_NOT_FOUND", "conversation not found", false)
	case errors.Is(err, conversation.ErrForbidden):
		WriteError(w, r, http.StatusForbidden, "FORBIDDEN", "conversation belongs to another customer", false)
	default:
		WriteError(w, r, http.StatusInternalServerError, "CONVERSATION_ACCESS_FAILED", "unable to verify conversation access", true)
	}
}
