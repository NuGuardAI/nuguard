package agent

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/mcpserver"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestMockEngineRunsEvidencePathAndRequiresReview(t *testing.T) {
	citation := domain.Citation{
		ChunkID: uuid.New(), Title: "subscription-activation.md", Locator: "Expected activation time",
		Content: "A paid plan may remain processing for up to fifteen minutes.", Score: .03,
	}
	searcher := &searcherStub{citations: []domain.Citation{citation}}
	business := &businessStub{
		account: mcpserver.AccountStatus{
			AccountID: "acct_demo_pro", Plan: "pro", AccountStatus: "active",
			SubscriptionSyncState: "processing", UpdatedAt: time.Now().UTC(),
		},
		payment: mcpserver.PaymentStatus{
			PaymentID: "pay_demo_paid", AccountID: "acct_demo_pro", Status: "paid",
			AmountCents: 2900, Currency: "USD", LinkedSubscription: "pro",
		},
		service: mcpserver.ServiceStatus{
			Service: "subscription-sync", Status: "degraded",
			ImpactScope: "Some updates may take longer.", Summary: "Generated queue delay.",
		},
	}
	engine := NewMockEngine(searcher, business, NewGuard(.70))
	sink := &collectingSink{}
	input := Input{
		ConversationID: uuid.New(), MessageID: uuid.New(), RunID: uuid.New(), CustomerID: uuid.New(),
		Text: "My Pro payment succeeded but workspace is still Free",
	}

	draft, err := engine.Run(context.Background(), input, sink)

	require.NoError(t, err)
	assert.Equal(t, domain.PriorityP1, draft.Priority)
	assert.Equal(t, "billing.subscription_activation", draft.Intent)
	assert.Equal(t, []domain.Citation{citation}, draft.Citations)
	require.NotEmpty(t, draft.Facts)
	assert.Equal(t, "get_payment_status", draft.Facts[0].Source)
	assert.Equal(t, "paid", draft.Facts[0].Value)
	assert.True(t, draft.RequiresReview)
	assert.Contains(t, draft.ReviewReasons, "HIGH_PRIORITY")
	assert.Contains(t, draft.Answer, "paid")
	assert.Contains(t, draft.Answer, "processing")
	assert.Equal(t, 1, searcher.calls)
	assert.Equal(t, "payment processing subscription", searcher.query)
	assert.Equal(t, 1, business.paymentCalls)
	assertEventSubsequence(t, sink.events, []domain.EventType{
		domain.EventRunStarted,
		domain.EventStageStarted,
		domain.EventRetrievalCompleted,
		domain.EventToolCompleted,
		domain.EventCitation,
		domain.EventDraftDelta,
		domain.EventReviewRequired,
	})
}

func TestMockEngineStopsWhenToolEventCannotBePersisted(t *testing.T) {
	persistErr := errors.New("persist event")
	searcher := &searcherStub{citations: []domain.Citation{{ChunkID: uuid.New(), Title: "guide", Locator: "Timing", Content: "Generated guidance."}}}
	business := &businessStub{}
	engine := NewMockEngine(searcher, business, NewGuard(.70))
	sink := sinkFunc(func(_ context.Context, event domain.AgentEvent) error {
		if event.Type == domain.EventToolStarted {
			return persistErr
		}
		return nil
	})
	input := Input{
		ConversationID: uuid.New(), MessageID: uuid.New(), RunID: uuid.New(), CustomerID: uuid.New(),
		Text: "My Pro payment succeeded but workspace is still Free",
	}

	_, err := engine.Run(context.Background(), input, sink)

	assert.ErrorIs(t, err, persistErr)
	assert.Zero(t, business.accountCalls)
	assert.Zero(t, business.paymentCalls)
}

type searcherStub struct {
	citations []domain.Citation
	err       error
	calls     int
	query     string
}

func (s *searcherStub) Search(_ context.Context, query string, _ int) ([]domain.Citation, error) {
	s.calls++
	s.query = query
	return s.citations, s.err
}

type businessStub struct {
	account      mcpserver.AccountStatus
	payment      mcpserver.PaymentStatus
	service      mcpserver.ServiceStatus
	accountErr   error
	paymentErr   error
	serviceErr   error
	accountCalls int
	paymentCalls int
	serviceCalls int
}

func (s *businessStub) GetAccountStatus(context.Context, mcpserver.AccountStatusInput) (mcpserver.AccountStatus, error) {
	s.accountCalls++
	return s.account, s.accountErr
}

func (s *businessStub) GetPaymentStatus(context.Context, mcpserver.PaymentStatusInput) (mcpserver.PaymentStatus, error) {
	s.paymentCalls++
	return s.payment, s.paymentErr
}

func (s *businessStub) GetServiceStatus(context.Context, mcpserver.ServiceStatusInput) (mcpserver.ServiceStatus, error) {
	s.serviceCalls++
	return s.service, s.serviceErr
}

type collectingSink struct {
	events []domain.AgentEvent
}

type sinkFunc func(context.Context, domain.AgentEvent) error

func (f sinkFunc) Emit(ctx context.Context, event domain.AgentEvent) error {
	return f(ctx, event)
}

func (s *collectingSink) Emit(_ context.Context, event domain.AgentEvent) error {
	s.events = append(s.events, event)
	return nil
}

func assertEventSubsequence(t *testing.T, events []domain.AgentEvent, expected []domain.EventType) {
	t.Helper()
	position := 0
	for _, event := range events {
		if position < len(expected) && event.Type == expected[position] {
			position++
		}
	}
	assert.Equal(t, len(expected), position, "event sequence: %v", events)
}
