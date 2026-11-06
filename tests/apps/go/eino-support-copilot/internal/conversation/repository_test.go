package conversation

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/testsupport"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestRepositoryPersistsConversationAndMessages(t *testing.T) {
	ctx := context.Background()
	pool := testsupport.StartPostgres(t)
	customerID := testsupport.InsertUser(t, pool, "customer", "Ada Demo")
	repository := NewRepository(pool)

	created, err := repository.Create(ctx, customerID, "Subscription issue")
	require.NoError(t, err)
	message, err := repository.AppendMessage(ctx, created.ID, domain.RoleUser, "Payment succeeded")
	require.NoError(t, err)

	got, err := repository.Get(ctx, created.ID)
	require.NoError(t, err)
	require.Len(t, got.Messages, 1)
	assert.Equal(t, message.ID, got.Messages[0].ID)
	assert.Equal(t, "Payment succeeded", got.Messages[0].Content)
	assert.Equal(t, domain.RoleUser, got.Messages[0].Role)

	listed, err := repository.List(ctx, customerID)
	require.NoError(t, err)
	require.Len(t, listed, 1)
	assert.Equal(t, created.ID, listed[0].ID)
}

func TestAppendMessageReturnsNotFoundForUnknownConversation(t *testing.T) {
	pool := testsupport.StartPostgres(t)
	repository := NewRepository(pool)

	_, err := repository.AppendMessage(context.Background(), uuid.New(), domain.RoleUser, "orphan")

	require.ErrorIs(t, err, ErrNotFound)
}
