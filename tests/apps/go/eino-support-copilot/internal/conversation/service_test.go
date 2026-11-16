package conversation

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/agent"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/runtrace"
	"github.com/guiyangyuan/eino-support-copilot/internal/testsupport"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSendSafeDraftPersistsAssistantAndCompletesRun(t *testing.T) {
	pool := testsupport.StartPostgres(t)
	customerID := testsupport.InsertUser(t, pool, "customer", "Ada Demo")
	repository := NewRepository(pool)
	conversationRecord, err := repository.Create(context.Background(), customerID, "General question")
	require.NoError(t, err)
	engine := &engineStub{draft: supportedDraft(false, domain.PriorityP2)}
	service := NewService(repository, runtrace.NewRepository(pool), engine)

	result, err := service.Send(context.Background(), customerID, conversationRecord.ID, "Is my account active?", &sinkStub{})

	require.NoError(t, err)
	assert.Equal(t, domain.RunCompleted, result.Run.Status)
	got, err := repository.Get(context.Background(), conversationRecord.ID)
	require.NoError(t, err)
	require.Len(t, got.Messages, 2)
	assert.Equal(t, domain.RoleUser, got.Messages[0].Role)
	assert.Equal(t, domain.RoleAssistant, got.Messages[1].Role)
}

func TestSendReviewDraftDoesNotPersistFinalAssistant(t *testing.T) {
	pool := testsupport.StartPostgres(t)
	customerID := testsupport.InsertUser(t, pool, "customer", "Ada Demo")
	repository := NewRepository(pool)
	conversationRecord, err := repository.Create(context.Background(), customerID, "Billing question")
	require.NoError(t, err)
	engine := &engineStub{draft: supportedDraft(true, domain.PriorityP1)}
	service := NewService(repository, runtrace.NewRepository(pool), engine)

	result, err := service.Send(context.Background(), customerID, conversationRecord.ID, "Payment succeeded", &sinkStub{})

	require.NoError(t, err)
	assert.Equal(t, domain.RunWaitingReview, result.Run.Status)
	got, err := repository.Get(context.Background(), conversationRecord.ID)
	require.NoError(t, err)
	require.Len(t, got.Messages, 1)
	assert.Equal(t, domain.RoleUser, got.Messages[0].Role)
}

func TestSendEngineFailureKeepsUserMessageAndFailsRun(t *testing.T) {
	pool := testsupport.StartPostgres(t)
	customerID := testsupport.InsertUser(t, pool, "customer", "Ada Demo")
	repository := NewRepository(pool)
	conversationRecord, err := repository.Create(context.Background(), customerID, "Failure case")
	require.NoError(t, err)
	engineErr := errors.New("model unavailable")
	service := NewService(repository, runtrace.NewRepository(pool), &engineStub{err: engineErr})

	result, err := service.Send(context.Background(), customerID, conversationRecord.ID, "Please check", &sinkStub{})

	assert.ErrorIs(t, err, engineErr)
	assert.Equal(t, domain.RunFailed, result.Run.Status)
	got, getErr := repository.Get(context.Background(), conversationRecord.ID)
	require.NoError(t, getErr)
	require.Len(t, got.Messages, 1)
	assert.Equal(t, domain.RoleUser, got.Messages[0].Role)
}

func TestSendRejectsConversationOwnedByAnotherCustomer(t *testing.T) {
	pool := testsupport.StartPostgres(t)
	ownerID := testsupport.InsertUser(t, pool, "customer", "Owner")
	otherID := testsupport.InsertUser(t, pool, "customer", "Other")
	repository := NewRepository(pool)
	conversationRecord, err := repository.Create(context.Background(), ownerID, "Private demo")
	require.NoError(t, err)
	service := NewService(repository, runtrace.NewRepository(pool), &engineStub{})

	_, err = service.Send(context.Background(), otherID, conversationRecord.ID, "read", &sinkStub{})

	assert.ErrorIs(t, err, ErrForbidden)
}

func supportedDraft(review bool, priority domain.Priority) domain.Draft {
	return domain.Draft{
		Intent: "account.general", Priority: priority, Answer: "The generated account is active.",
		Citations:  []domain.Citation{{ChunkID: uuid.New(), Title: "Guide", Locator: "Status", Content: "Active means available."}},
		Facts:      []domain.Fact{{Source: "get_account_status", Field: "account_status", Value: "active"}},
		Confidence: .9, RequiresReview: review,
	}
}

type engineStub struct {
	draft domain.Draft
	err   error
}

func (e *engineStub) Run(_ context.Context, _ agent.Input, _ runtrace.Sink) (domain.Draft, error) {
	return e.draft, e.err
}

type sinkStub struct{}

func (s *sinkStub) Emit(context.Context, domain.AgentEvent) error { return nil }
