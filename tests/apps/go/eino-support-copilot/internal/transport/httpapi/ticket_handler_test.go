package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/ticket"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestReviewTicketRequiresAgentAndReturnsAudit(t *testing.T) {
	ticketID := uuid.New()
	tickets := &ticketServiceStub{review: domain.TicketReview{ID: uuid.New(), TicketID: ticketID, Action: domain.ReviewApprove}}

	for _, test := range []struct {
		name   string
		role   string
		status int
	}{
		{name: "customer forbidden", role: "customer", status: http.StatusForbidden},
		{name: "agent approved", role: "agent", status: http.StatusOK},
	} {
		t.Run(test.name, func(t *testing.T) {
			handler := NewServer(Dependencies{Mode: "mock", Tickets: tickets, Identity: &identityStub{id: uuid.New(), role: test.role}})
			recorder := httptest.NewRecorder()
			request := httptest.NewRequest(http.MethodPost, "/api/v1/tickets/"+ticketID.String()+"/review", bytes.NewBufferString(`{"action":"approve","final_answer":"Approved generated answer"}`))
			request.Header.Set("Authorization", "Bearer "+"demo-user") // NuGuard fixture: was X-Demo-User

			handler.ServeHTTP(recorder, request)

			require.Equal(t, test.status, recorder.Code)
		})
	}
	assert.Equal(t, 1, tickets.reviewCalls)
}

func TestListTicketsRejectsUnknownFilter(t *testing.T) {
	handler := NewServer(Dependencies{
		Mode: "mock", Tickets: &ticketServiceStub{},
		Identity: &identityStub{id: uuid.New(), role: "agent"},
	})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/tickets?status=unknown", nil)
	request.Header.Set("Authorization", "Bearer "+"demo-agent") // NuGuard fixture: was X-Demo-User

	handler.ServeHTTP(recorder, request)

	require.Equal(t, http.StatusUnprocessableEntity, recorder.Code)
	var response ErrorResponse
	require.NoError(t, json.NewDecoder(recorder.Body).Decode(&response))
	assert.Equal(t, "INVALID_FILTER", response.Code)
}

type ticketServiceStub struct {
	review      domain.TicketReview
	reviewCalls int
	detail      ticket.Detail
}

func (s *ticketServiceStub) CreateFromConversation(context.Context, uuid.UUID) (domain.Ticket, error) {
	return domain.Ticket{}, nil
}

func (s *ticketServiceStub) Get(context.Context, uuid.UUID) (domain.Ticket, error) {
	return domain.Ticket{}, nil
}

func (s *ticketServiceStub) GetDetail(context.Context, uuid.UUID) (ticket.Detail, error) {
	return s.detail, nil
}

func (s *ticketServiceStub) List(context.Context, ticket.ListFilter) ([]domain.Ticket, error) {
	return []domain.Ticket{}, nil
}

func (s *ticketServiceStub) Review(context.Context, ticket.ReviewCommand) (domain.TicketReview, error) {
	s.reviewCalls++
	return s.review, nil
}
