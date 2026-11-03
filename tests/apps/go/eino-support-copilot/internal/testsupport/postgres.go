package testsupport

import (
	"context"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/platform/database"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/stretchr/testify/require"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/modules/postgres"
	"github.com/testcontainers/testcontainers-go/wait"
)

func ExternalDatabaseURL(t *testing.T) (string, bool) {
	t.Helper()
	serverURL := os.Getenv("TEST_DATABASE_URL")
	if serverURL == "" {
		return "", false
	}
	ctx := context.Background()
	name := "test_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	adminExec(t, ctx, serverURL, "create database "+name)
	t.Cleanup(func() {
		adminExec(t, ctx, serverURL, "drop database if exists "+name+" with (force)")
	})

	databaseURL, err := url.Parse(serverURL)
	require.NoError(t, err)
	databaseURL.Path = "/" + name
	return databaseURL.String(), true
}

func adminExec(t *testing.T, ctx context.Context, serverURL, statement string) {
	t.Helper()
	conn, err := pgx.Connect(ctx, serverURL)
	require.NoError(t, err)
	defer conn.Close(ctx)
	_, err = conn.Exec(ctx, statement)
	require.NoError(t, err)
}

func StartPostgres(t *testing.T) *pgxpool.Pool {
	t.Helper()
	ctx := context.Background()
	if databaseURL, ok := ExternalDatabaseURL(t); ok {
		pool, err := database.Open(ctx, databaseURL)
		require.NoError(t, err)
		t.Cleanup(pool.Close)
		require.NoError(t, database.Migrate(ctx, pool, migrationsDirectory(t)))
		return pool
	}
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
	pool, err := database.Open(ctx, connectionString)
	require.NoError(t, err)
	t.Cleanup(pool.Close)
	require.NoError(t, database.Migrate(ctx, pool, migrationsDirectory(t)))
	return pool
}

func InsertUser(t *testing.T, pool *pgxpool.Pool, role, name string) uuid.UUID {
	t.Helper()
	var id uuid.UUID
	require.NoError(t, pool.QueryRow(context.Background(),
		`insert into demo_users (role, display_name) values ($1, $2) returning id`, role, name,
	).Scan(&id))
	return id
}

func migrationsDirectory(t *testing.T) string {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	require.True(t, ok)
	return filepath.Clean(filepath.Join(filepath.Dir(filename), "..", "..", "migrations"))
}
