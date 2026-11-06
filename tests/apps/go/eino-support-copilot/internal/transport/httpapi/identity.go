package httpapi

import (
	"context"
	"errors"
	"strings"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/platform/database"
	"github.com/jackc/pgx/v5"
)

var ErrIdentityNotFound = errors.New("demo identity not found")

type PostgresIdentityResolver struct {
	db database.DBTX
}

func NewPostgresIdentityResolver(db database.DBTX) *PostgresIdentityResolver {
	return &PostgresIdentityResolver{db: db}
}

func (r *PostgresIdentityResolver) Resolve(ctx context.Context, raw string) (uuid.UUID, string, error) {
	id, err := uuid.Parse(strings.TrimSpace(raw))
	if err != nil {
		return uuid.Nil, "", ErrIdentityNotFound
	}
	var role string
	if err := r.db.QueryRow(ctx, `select role from demo_users where id = $1`, id).Scan(&role); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return uuid.Nil, "", ErrIdentityNotFound
		}
		return uuid.Nil, "", err
	}
	return id, role, nil
}
