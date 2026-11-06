package httpapi

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/ticket"
)

func createTicket(service TicketService, conversations ConversationService, identity IdentityResolver) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		customerID, ok := requireDemoRole(w, r, identity, "customer")
		if !ok {
			return
		}
		if service == nil || conversations == nil {
			WriteError(w, r, http.StatusServiceUnavailable, "TICKET_UNAVAILABLE", "ticket service is unavailable", true)
			return
		}
		conversationID, err := uuid.Parse(r.PathValue("id"))
		if err != nil {
			WriteError(w, r, http.StatusNotFound, "CONVERSATION_NOT_FOUND", "conversation not found", false)
			return
		}
		if err := conversations.CanAccess(r.Context(), customerID, conversationID); err != nil {
			writeConversationAccessError(w, r, err)
			return
		}
		created, err := service.CreateFromConversation(r.Context(), conversationID)
		if err != nil {
			writeTicketError(w, r, err)
			return
		}
		writeJSON(w, http.StatusCreated, created)
	}
}

func listTickets(service TicketService, identity IdentityResolver) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if _, ok := requireDemoRole(w, r, identity, "agent"); !ok {
			return
		}
		if service == nil {
			WriteError(w, r, http.StatusServiceUnavailable, "TICKET_UNAVAILABLE", "ticket service is unavailable", true)
			return
		}
		filter, ok := parseTicketFilter(r)
		if !ok {
			WriteError(w, r, http.StatusUnprocessableEntity, "INVALID_FILTER", "status or priority filter is invalid", false)
			return
		}
		items, err := service.List(r.Context(), filter)
		if err != nil {
			WriteError(w, r, http.StatusInternalServerError, "TICKET_LIST_FAILED", "unable to list tickets", true)
			return
		}
		writeJSON(w, http.StatusOK, items)
	}
}

func getTicket(service TicketService, identity IdentityResolver) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if _, ok := requireDemoRole(w, r, identity, "agent"); !ok {
			return
		}
		if service == nil {
			WriteError(w, r, http.StatusServiceUnavailable, "TICKET_UNAVAILABLE", "ticket service is unavailable", true)
			return
		}
		ticketID, err := uuid.Parse(r.PathValue("id"))
		if err != nil {
			WriteError(w, r, http.StatusNotFound, "TICKET_NOT_FOUND", "ticket not found", false)
			return
		}
		item, err := service.GetDetail(r.Context(), ticketID)
		if err != nil {
			writeTicketError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, item)
	}
}

func reviewTicket(service TicketService, identity IdentityResolver) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		reviewerID, ok := requireDemoRole(w, r, identity, "agent")
		if !ok {
			return
		}
		if service == nil {
			WriteError(w, r, http.StatusServiceUnavailable, "TICKET_UNAVAILABLE", "ticket service is unavailable", true)
			return
		}
		ticketID, err := uuid.Parse(r.PathValue("id"))
		if err != nil {
			WriteError(w, r, http.StatusNotFound, "TICKET_NOT_FOUND", "ticket not found", false)
			return
		}
		var request struct {
			Action      domain.ReviewAction `json:"action"`
			FinalAnswer string              `json:"final_answer"`
		}
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10)).Decode(&request); err != nil {
			WriteError(w, r, http.StatusBadRequest, "INVALID_JSON", "request body must be valid JSON", false)
			return
		}
		review, err := service.Review(r.Context(), ticket.ReviewCommand{
			TicketID: ticketID, ReviewerID: reviewerID, Action: request.Action,
			FinalAnswer: request.FinalAnswer,
		})
		if err != nil {
			writeTicketError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, review)
	}
}

func parseTicketFilter(r *http.Request) (ticket.ListFilter, bool) {
	var filter ticket.ListFilter
	if raw := strings.TrimSpace(r.URL.Query().Get("status")); raw != "" {
		status := domain.TicketStatus(raw)
		switch status {
		case domain.TicketOpen, domain.TicketTriaged, domain.TicketWaitingReview, domain.TicketResolved, domain.TicketEscalated:
			filter.Status = &status
		default:
			return ticket.ListFilter{}, false
		}
	}
	if raw := strings.TrimSpace(r.URL.Query().Get("priority")); raw != "" {
		priority := domain.Priority(raw)
		switch priority {
		case domain.PriorityP0, domain.PriorityP1, domain.PriorityP2, domain.PriorityP3:
			filter.Priority = &priority
		default:
			return ticket.ListFilter{}, false
		}
	}
	return filter, true
}

func writeTicketError(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, ticket.ErrNotFound):
		WriteError(w, r, http.StatusNotFound, "TICKET_NOT_FOUND", "ticket not found", false)
	case errors.Is(err, ticket.ErrNoReviewableRun):
		WriteError(w, r, http.StatusConflict, "NO_REVIEWABLE_RUN", "conversation has no answer waiting for review", false)
	case errors.Is(err, ticket.ErrReviewerForbidden):
		WriteError(w, r, http.StatusForbidden, "FORBIDDEN", "agent role is required", false)
	case errors.Is(err, ticket.ErrFinalAnswerRequired):
		WriteError(w, r, http.StatusUnprocessableEntity, "FINAL_ANSWER_REQUIRED", "final answer is required", false)
	case errors.Is(err, ticket.ErrInvalidReviewAction):
		WriteError(w, r, http.StatusUnprocessableEntity, "INVALID_REVIEW_ACTION", "review action is invalid", false)
	case errors.Is(err, ticket.ErrInvalidTransition):
		WriteError(w, r, http.StatusConflict, "INVALID_TICKET_STATE", "ticket is not waiting for review", false)
	default:
		WriteError(w, r, http.StatusInternalServerError, "TICKET_OPERATION_FAILED", "ticket operation failed", true)
	}
}
