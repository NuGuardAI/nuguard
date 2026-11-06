package ticket

import (
	"context"
	"testing"

	"github.com/guiyangyuan/eino-support-copilot/internal/conversation"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/testsupport"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestReviewAppendsAuditAndResolvesWaitingTicket(t *testing.T) {
	ctx := context.Background()
	pool := testsupport.StartPostgres(t)
	customerID := testsupport.InsertUser(t, pool, "customer", "Ada Demo")
	reviewerID := testsupport.InsertUser(t, pool, "agent", "Lin Agent")
	conversationRepository := conversation.NewRepository(pool)
	conversationRecord, err := conversationRepository.Create(ctx, customerID, "Subscription issue")
	require.NoError(t, err)
	repository := NewRepository(pool)
	ticketRecord, err := repository.Create(ctx, CreateParams{
		ConversationID: conversationRecord.ID,
		Status:         domain.TicketWaitingReview,
		Priority:       domain.PriorityP1,
		Intent:         "billing.subscription_activation",
		Summary:        "Paid Pro plan has not activated",
		Draft:          &domain.Draft{Answer: "Payment is confirmed; subscription sync is still processing."},
	})
	require.NoError(t, err)

	review, err := repository.Review(ctx, ReviewParams{
		TicketID:    ticketRecord.ID,
		ReviewerID:  reviewerID,
		Action:      domain.ReviewApprove,
		FinalAnswer: "Payment is confirmed. Your Pro subscription is still syncing.",
	})
	require.NoError(t, err)
	assert.Equal(t, domain.ReviewApprove, review.Action)

	got, err := repository.Get(ctx, ticketRecord.ID)
	require.NoError(t, err)
	assert.Equal(t, domain.TicketResolved, got.Status)
	require.Len(t, got.Reviews, 1)
	assert.Equal(t, review.ID, got.Reviews[0].ID)

	_, err = repository.Review(ctx, ReviewParams{
		TicketID: ticketRecord.ID, ReviewerID: reviewerID, Action: domain.ReviewApprove, FinalAnswer: "duplicate",
	})
	require.ErrorIs(t, err, ErrInvalidTransition)
}

func TestListFiltersTicketsByStatus(t *testing.T) {
	ctx := context.Background()
	pool := testsupport.StartPostgres(t)
	customerID := testsupport.InsertUser(t, pool, "customer", "Ada Demo")
	conversationRepository := conversation.NewRepository(pool)
	repository := NewRepository(pool)
	waitingConversation, err := conversationRepository.Create(ctx, customerID, "Waiting")
	require.NoError(t, err)
	resolvedConversation, err := conversationRepository.Create(ctx, customerID, "Resolved")
	require.NoError(t, err)
	waiting, err := repository.Create(ctx, CreateParams{
		ConversationID: waitingConversation.ID, Status: domain.TicketWaitingReview,
		Priority: domain.PriorityP1, Intent: "billing", Summary: "waiting",
	})
	require.NoError(t, err)
	_, err = repository.Create(ctx, CreateParams{
		ConversationID: resolvedConversation.ID, Status: domain.TicketResolved,
		Priority: domain.PriorityP2, Intent: "billing", Summary: "resolved",
	})
	require.NoError(t, err)
	status := domain.TicketWaitingReview

	tickets, err := repository.List(ctx, ListFilter{Status: &status})

	require.NoError(t, err)
	require.Len(t, tickets, 1)
	assert.Equal(t, waiting.ID, tickets[0].ID)
}

func TestListOrdersTicketsByOperationalPriority(t *testing.T) {
	ctx := context.Background()
	pool := testsupport.StartPostgres(t)
	customerID := testsupport.InsertUser(t, pool, "customer", "Ada Demo")
	conversationRepository := conversation.NewRepository(pool)
	repository := NewRepository(pool)
	for _, priority := range []domain.Priority{domain.PriorityP2, domain.PriorityP0, domain.PriorityP1} {
		conversationRecord, err := conversationRepository.Create(ctx, customerID, string(priority))
		require.NoError(t, err)
		_, err = repository.Create(ctx, CreateParams{
			ConversationID: conversationRecord.ID, Status: domain.TicketResolved,
			Priority: priority, Intent: "test", Summary: string(priority),
		})
		require.NoError(t, err)
	}

	tickets, err := repository.List(ctx, ListFilter{})

	require.NoError(t, err)
	require.Len(t, tickets, 3)
	assert.Equal(t, []domain.Priority{domain.PriorityP0, domain.PriorityP1, domain.PriorityP2},
		[]domain.Priority{tickets[0].Priority, tickets[1].Priority, tickets[2].Priority})
}
