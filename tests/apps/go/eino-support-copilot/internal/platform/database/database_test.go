package database

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/modules/postgres"
	"github.com/testcontainers/testcontainers-go/wait"
)

func TestMigrateCreatesCompleteSchema(t *testing.T) {
	ctx := context.Background()
	pool := startPostgres(t, ctx)

	require.NoError(t, Migrate(ctx, pool, filepath.Join("..", "..", "..", "migrations")))
	require.NoError(t, Migrate(ctx, pool, filepath.Join("..", "..", "..", "migrations")), "migration must be idempotent")
	assert.Equal(t, int32(10), pool.Config().MaxConns)
	assert.Equal(t, int32(1), pool.Config().MinConns)
	assert.Equal(t, 30*time.Minute, pool.Config().MaxConnLifetime)

	var vectorInstalled bool
	require.NoError(t, pool.QueryRow(ctx,
		`select exists(select 1 from pg_extension where extname = 'vector')`,
	).Scan(&vectorInstalled))
	assert.True(t, vectorInstalled)

	tables := []string{
		"demo_users", "conversations", "messages", "tickets", "ticket_reviews",
		"knowledge_documents", "knowledge_chunks", "agent_runs", "agent_run_steps",
		"eval_datasets", "eval_cases", "eval_runs", "eval_results",
		"mock_accounts", "mock_payments", "mock_service_incidents",
	}
	for _, table := range tables {
		var exists bool
		require.NoError(t, pool.QueryRow(ctx,
			`select to_regclass($1) is not null`, "public."+table,
		).Scan(&exists))
		assert.Truef(t, exists, "table %s must exist", table)
	}
}

func TestMigrateEnforcesRunStatus(t *testing.T) {
	ctx := context.Background()
	pool := startPostgres(t, ctx)
	require.NoError(t, Migrate(ctx, pool, filepath.Join("..", "..", "..", "migrations")))

	var customerID uuid.UUID
	require.NoError(t, pool.QueryRow(ctx,
		`insert into demo_users (role, display_name) values ('customer', 'Demo Customer') returning id`,
	).Scan(&customerID))
	var conversationID uuid.UUID
	require.NoError(t, pool.QueryRow(ctx,
		`insert into conversations (customer_id, title) values ($1, 'Status constraint') returning id`,
		customerID,
	).Scan(&conversationID))

	_, err := pool.Exec(ctx, `
		insert into agent_runs (conversation_id, status, mode, input_summary)
		values ($1, 'thinking', 'mock', 'invalid status')
	`, conversationID)

	var postgresError *pgconn.PgError
	require.True(t, errors.As(err, &postgresError))
	assert.Equal(t, "agent_runs_status_valid", postgresError.ConstraintName)
}

func startPostgres(t *testing.T, ctx context.Context) *pgxpool.Pool {
	t.Helper()

	container, err := postgres.Run(ctx,
		"pgvector/pgvector:pg17",
		postgres.WithDatabase("copilot"),
		postgres.WithUsername("copilot"),
		postgres.WithPassword("copilot"),
		testcontainers.WithWaitStrategy(
			wait.ForLog("database system is ready to accept connections").
				WithOccurrence(2).
				WithStartupTimeout(2*time.Minute),
		),
	)
	require.NoError(t, err)
	t.Cleanup(func() {
		require.NoError(t, testcontainers.TerminateContainer(container))
	})

	connectionString, err := container.ConnectionString(ctx, "sslmode=disable")
	require.NoError(t, err)
	pool, err := Open(ctx, connectionString)
	require.NoError(t, err)
	t.Cleanup(pool.Close)
	return pool
}
