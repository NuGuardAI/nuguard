package ticket

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/guiyangyuan/eino-support-copilot/internal/conversation"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/runtrace"
	"github.com/guiyangyuan/eino-support-copilot/internal/testsupport"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestCreateTicketCopiesWaitingReviewDraftAndIsIdempotent(t *testing.T) {
	ctx := context.Background()
	pool := testsupport.StartPostgres(t)
	customerID := testsupport.InsertUser(t, pool, "customer", "Ada Demo")
	conversationRecord, err := conversation.NewRepository(pool).Create(ctx, customerID, "Billing")
	require.NoError(t, err)
	draft := domain.Draft{
		Intent: "billing.subscription_activation", Priority: domain.PriorityP1,
		Answer: "Payment paid; synchronization processing.", Confidence: .94, RequiresReview: true,
	}
	encoded, err := json.Marshal(draft)
	require.NoError(t, err)
	runs := runtrace.NewRepository(pool)
	run, err := runs.Start(ctx, runtrace.StartParams{
		ConversationID: conversationRecord.ID, Status: domain.RunRunning, Mode: "mock", InputSummary: "billing",
	})
	require.NoError(t, err)
	require.NoError(t, runs.Complete(ctx, run.ID, domain.RunWaitingReview, string(encoded), json.RawMessage(`{}`)))
	service := NewService(pool)

	created, err := service.CreateFromConversation(ctx, conversationRecord.ID)
	require.NoError(t, err)
	again, err := service.CreateFromConversation(ctx, conversationRecord.ID)
	require.NoError(t, err)

	assert.Equal(t, created.ID, again.ID)
	assert.Equal(t, draft.Intent, created.Intent)
	assert.Equal(t, draft.Priority, created.Priority)
	require.NotNil(t, created.Draft)
	assert.Equal(t, draft.Answer, created.Draft.Answer)
	var linkedTicketID string
	require.NoError(t, pool.QueryRow(ctx, `select ticket_id::text from agent_runs where id = $1`, run.ID).Scan(&linkedTicketID))
	assert.Equal(t, created.ID.String(), linkedTicketID)
}

func TestReviewTicketApprovesAtomicallyAndRequiresAgentRole(t *testing.T) {
	ctx := context.Background()
	pool := testsupport.StartPostgres(t)
	customerID := testsupport.InsertUser(t, pool, "customer", "Ada Demo")
	agentID := testsupport.InsertUser(t, pool, "agent", "Lin Agent")
	conversationRecord, err := conversation.NewRepository(pool).Create(ctx, customerID, "Billing")
	require.NoError(t, err)
	repository := NewRepository(pool)
	ticketRecord, err := repository.Create(ctx, CreateParams{
		ConversationID: conversationRecord.ID, Status: domain.TicketWaitingReview,
		Priority: domain.PriorityP1, Intent: "billing", Summary: "review",
		Draft: &domain.Draft{Answer: "Draft"},
	})
	require.NoError(t, err)
	runs := runtrace.NewRepository(pool)
	run, err := runs.Start(ctx, runtrace.StartParams{
		ConversationID: conversationRecord.ID, TicketID: &ticketRecord.ID,
		Status: domain.RunWaitingReview, Mode: "mock", InputSummary: "billing",
	})
	require.NoError(t, err)
	service := NewService(pool)

	_, err = service.Review(ctx, ReviewCommand{
		TicketID: ticketRecord.ID, ReviewerID: customerID, Action: domain.ReviewApprove, FinalAnswer: "Approved",
	})
	assert.ErrorIs(t, err, ErrReviewerForbidden)

	review, err := service.Review(ctx, ReviewCommand{
		TicketID: ticketRecord.ID, ReviewerID: agentID, Action: domain.ReviewApprove,
		FinalAnswer: "Payment is confirmed; synchronization is processing.",
	})
	require.NoError(t, err)
	assert.Equal(t, domain.ReviewApprove, review.Action)
	got, err := repository.Get(ctx, ticketRecord.ID)
	require.NoError(t, err)
	assert.Equal(t, domain.TicketResolved, got.Status)
	completedRun, err := runs.Get(ctx, run.ID)
	require.NoError(t, err)
	assert.Equal(t, domain.RunCompleted, completedRun.Status)
	conversationAfter, err := conversation.NewRepository(pool).Get(ctx, conversationRecord.ID)
	require.NoError(t, err)
	require.Len(t, conversationAfter.Messages, 1)
	assert.Equal(t, domain.RoleAgent, conversationAfter.Messages[0].Role)

	editTicket, err := repository.Create(ctx, CreateParams{
		ConversationID: conversationRecord.ID, Status: domain.TicketWaitingReview,
		Priority: domain.PriorityP2, Intent: "account", Summary: "edit", Draft: &domain.Draft{Answer: "Draft to edit"},
	})
	require.NoError(t, err)
	editRun, err := runs.Start(ctx, runtrace.StartParams{
		ConversationID: conversationRecord.ID, TicketID: &editTicket.ID,
		Status: domain.RunWaitingReview, Mode: "mock", InputSummary: "account",
	})
	require.NoError(t, err)
	_, err = service.Review(ctx, ReviewCommand{
		TicketID: editTicket.ID, ReviewerID: agentID, Action: domain.ReviewEditAndApprove,
		FinalAnswer: "Edited and approved answer.",
	})
	require.NoError(t, err)
	completedEditRun, err := runs.Get(ctx, editRun.ID)
	require.NoError(t, err)
	assert.Equal(t, domain.RunCompleted, completedEditRun.Status)

	escalatedTicket, err := repository.Create(ctx, CreateParams{
		ConversationID: conversationRecord.ID, Status: domain.TicketWaitingReview,
		Priority: domain.PriorityP0, Intent: "risk", Summary: "escalate", Draft: &domain.Draft{Answer: "Unsafe to send"},
	})
	require.NoError(t, err)
	escalatedRun, err := runs.Start(ctx, runtrace.StartParams{
		ConversationID: conversationRecord.ID, TicketID: &escalatedTicket.ID,
		Status: domain.RunWaitingReview, Mode: "mock", InputSummary: "risk",
	})
	require.NoError(t, err)
	_, err = service.Review(ctx, ReviewCommand{
		TicketID: escalatedTicket.ID, ReviewerID: agentID, Action: domain.ReviewEscalate,
	})
	require.NoError(t, err)
	escalatedAfter, err := repository.Get(ctx, escalatedTicket.ID)
	require.NoError(t, err)
	assert.Equal(t, domain.TicketEscalated, escalatedAfter.Status)
	waitingRun, err := runs.Get(ctx, escalatedRun.ID)
	require.NoError(t, err)
	assert.Equal(t, domain.RunWaitingReview, waitingRun.Status)
}

func TestGetTicketDetailIncludesConversationLatestRunAndEvidence(t *testing.T) {
	ctx := context.Background()
	pool := testsupport.StartPostgres(t)
	customerID := testsupport.InsertUser(t, pool, "customer", "Ada Demo")
	conversationRecord, err := conversation.NewRepository(pool).Create(ctx, customerID, "Account access")
	require.NoError(t, err)
	draft := &domain.Draft{
		Intent: "account.status", Priority: domain.PriorityP2, Answer: "The account is active.",
		Citations: []domain.Citation{{Title: "Account policy", Locator: "section 2"}},
		Facts:     []domain.Fact{{Source: "get_account_status", Field: "status", Value: "active"}},
	}
	ticketRecord, err := NewRepository(pool).Create(ctx, CreateParams{
		ConversationID: conversationRecord.ID, Status: domain.TicketWaitingReview,
		Priority: draft.Priority, Intent: draft.Intent, Summary: draft.Answer, Draft: draft,
	})
	require.NoError(t, err)
	run, err := runtrace.NewRepository(pool).Start(ctx, runtrace.StartParams{
		ConversationID: conversationRecord.ID, TicketID: &ticketRecord.ID,
		Status: domain.RunWaitingReview, Mode: "mock", InputSummary: "account",
	})
	require.NoError(t, err)

	detail, err := NewService(pool).GetDetail(ctx, ticketRecord.ID)
	require.NoError(t, err)

	assert.Equal(t, ticketRecord.ID, detail.Ticket.ID)
	assert.Equal(t, conversationRecord.ID, detail.Conversation.ID)
	require.NotNil(t, detail.LatestRun)
	assert.Equal(t, run.ID, detail.LatestRun.ID)
	require.Len(t, detail.Citations, 1)
	assert.Equal(t, "Account policy", detail.Citations[0].Title)
	require.Len(t, detail.Facts, 1)
	assert.Equal(t, "status", detail.Facts[0].Field)
}
