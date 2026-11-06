package mcpserver

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/guiyangyuan/eino-support-copilot/internal/testsupport"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestGetPaymentStatusReturnsOnlySafeFields(t *testing.T) {
	pool := testsupport.StartPostgres(t)
	loadBusinessSeed(t, pool)
	service := NewService(pool)

	result, err := service.GetPaymentStatus(context.Background(), PaymentStatusInput{
		AccountID: "acct_demo_pro",
		PaymentID: "pay_demo_paid",
	})

	require.NoError(t, err)
	assert.Equal(t, "paid", result.Status)
	assert.Equal(t, "pro", result.LinkedSubscription)
	assert.Equal(t, int64(2900), result.AmountCents)
	encoded, err := json.Marshal(result)
	require.NoError(t, err)
	assert.NotContains(t, string(encoded), "card_number")
	assert.NotContains(t, string(encoded), "customer_name")
}

func TestGetAccountStatusRejectsEmptyAccountID(t *testing.T) {
	pool := testsupport.StartPostgres(t)
	service := NewService(pool)

	_, err := service.GetAccountStatus(context.Background(), AccountStatusInput{})

	assertToolError(t, err, "INVALID_ARGUMENT", false)
}

func TestGetServiceStatusReturnsNotFoundForUnknownService(t *testing.T) {
	pool := testsupport.StartPostgres(t)
	loadBusinessSeed(t, pool)
	service := NewService(pool)

	_, err := service.GetServiceStatus(context.Background(), ServiceStatusInput{Service: "unknown"})

	assertToolError(t, err, "NOT_FOUND", false)
}

func TestGetPaymentStatusMapsDeadlineToRetryableError(t *testing.T) {
	pool := testsupport.StartPostgres(t)
	service := NewService(pool)
	ctx, cancel := context.WithCancelCause(context.Background())
	cancel(context.DeadlineExceeded)

	_, err := service.GetPaymentStatus(ctx, PaymentStatusInput{AccountID: "acct_demo_pro"})

	assertToolError(t, err, "TEMPORARY_UNAVAILABLE", true)
}

func assertToolError(t *testing.T, err error, code string, retryable bool) {
	t.Helper()
	var toolErr *ToolError
	require.True(t, errors.As(err, &toolErr), "expected ToolError, got %v", err)
	assert.Equal(t, code, toolErr.Code)
	assert.Equal(t, retryable, toolErr.Retryable)
}

func loadBusinessSeed(t *testing.T, pool *pgxpool.Pool) {
	t.Helper()
	_, err := pool.Exec(context.Background(), businessSeed(t))
	require.NoError(t, err)
}

func businessSeed(t *testing.T) string {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	require.True(t, ok)
	data, err := os.ReadFile(filepath.Join(filepath.Dir(filename), "..", "..", "seed", "business.sql"))
	require.NoError(t, err)
	return string(data)
}
