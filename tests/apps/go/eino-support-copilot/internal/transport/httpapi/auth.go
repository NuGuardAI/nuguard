package httpapi

// NuGuard fixture: this file is not part of upstream.
//
// Upstream identifies the caller with an unauthenticated X-Demo-User: <uuid> header. For testing,
// that is replaced by JWT bearer auth for the seeded demo users:
//   POST /auth/token {"username","password"} -> {"access_token","token_type":"Bearer","user_id","role"}
// Passwords are per user, stored as bcrypt hashes in demo_users.password_hash (SeedDemoPasswords).
// A user without a hash cannot log in.
// Every /api/v1/* route and POST /chat read "Authorization: Bearer <jwt>" only (see
// requireDemoIdentity). The MCP tools are not changed, so the account_id ownership checks they
// lack remain.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/platform/database"
	"github.com/jackc/pgx/v5"
	"golang.org/x/crypto/bcrypt"
)

const (
	jwtIssuer        = "eino-support-copilot"
	jwtTokenLifetime = 4 * time.Hour
	minSigningKeyLen = 32
)

type demoClaims struct {
	Role string `json:"role"`
	jwt.RegisteredClaims
}

// JWTAuth issues and verifies HS256 tokens for demo_users. It implements IdentityResolver, taking
// the raw bearer token instead of the upstream header value.
type JWTAuth struct {
	db         database.DBTX
	users      *PostgresIdentityResolver
	signingKey []byte
	dummyHash  []byte // compared for unknown users so they take as long as a wrong password
}

func NewJWTAuth(db database.DBTX, signingKey string) (*JWTAuth, error) {
	if len(signingKey) < minSigningKeyLen {
		return nil, fmt.Errorf("JWT_SIGNING_KEY must be at least %d bytes", minSigningKeyLen)
	}
	dummyHash, err := bcrypt.GenerateFromPassword([]byte("nuguard-unknown-user"), bcrypt.DefaultCost)
	if err != nil {
		return nil, err
	}
	return &JWTAuth{
		db:         db,
		users:      NewPostgresIdentityResolver(db),
		signingKey: []byte(signingKey),
		dummyHash:  dummyHash,
	}, nil
}

// SeedDemoPasswords stores a bcrypt hash for each username -> password pair. An empty password
// clears the hash, so that user cannot log in.
func SeedDemoPasswords(ctx context.Context, db database.DBTX, passwords map[string]string) error {
	for username, password := range passwords {
		var hash *string
		if password != "" {
			encoded, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
			if err != nil {
				return fmt.Errorf("hash password for %s: %w", username, err)
			}
			value := string(encoded)
			hash = &value
		}
		if _, err := db.Exec(ctx, `update demo_users set password_hash = $2 where username = $1`, username, hash); err != nil {
			return fmt.Errorf("store password hash for %s: %w", username, err)
		}
	}
	return nil
}

// Resolve verifies the bearer token and returns the user id and the role stored in demo_users.
func (a *JWTAuth) Resolve(ctx context.Context, rawToken string) (uuid.UUID, string, error) {
	claims := &demoClaims{}
	_, err := jwt.ParseWithClaims(strings.TrimSpace(rawToken), claims, func(*jwt.Token) (any, error) {
		return a.signingKey, nil
	},
		jwt.WithValidMethods([]string{jwt.SigningMethodHS256.Alg()}),
		jwt.WithIssuer(jwtIssuer),
		jwt.WithExpirationRequired(),
	)
	if err != nil {
		return uuid.Nil, "", ErrIdentityNotFound
	}
	return a.users.Resolve(ctx, claims.Subject)
}

func (a *JWTAuth) issueToken(w http.ResponseWriter, r *http.Request) {
	var request struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 16<<10)).Decode(&request); err != nil {
		WriteError(w, r, http.StatusBadRequest, "INVALID_JSON", "request body must be valid JSON", false)
		return
	}
	var (
		userID       uuid.UUID
		role         string
		passwordHash *string
	)
	lookupErr := a.db.QueryRow(r.Context(), `select id, role, password_hash from demo_users where username = $1`,
		strings.ToLower(strings.TrimSpace(request.Username))).Scan(&userID, &role, &passwordHash)
	if lookupErr != nil && !errors.Is(lookupErr, pgx.ErrNoRows) {
		WriteError(w, r, http.StatusInternalServerError, "LOGIN_FAILED", "unable to verify credentials", true)
		return
	}
	// Unknown users and users without a password still pay for one bcrypt comparison, and every
	// failure returns the same 401 body.
	hash := a.dummyHash
	if lookupErr == nil && passwordHash != nil {
		hash = []byte(*passwordHash)
	}
	passwordOK := bcrypt.CompareHashAndPassword(hash, []byte(request.Password)) == nil
	if lookupErr != nil || passwordHash == nil || !passwordOK {
		WriteError(w, r, http.StatusUnauthorized, "INVALID_CREDENTIALS", "invalid username or password", false)
		return
	}

	now := time.Now()
	token, err := jwt.NewWithClaims(jwt.SigningMethodHS256, demoClaims{
		Role: role,
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer:    jwtIssuer,
			Subject:   userID.String(),
			IssuedAt:  jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(now.Add(jwtTokenLifetime)),
		},
	}).SignedString(a.signingKey)
	if err != nil {
		WriteError(w, r, http.StatusInternalServerError, "LOGIN_FAILED", "unable to issue token", true)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"access_token": token,
		"token_type":   "Bearer",
		"expires_in":   int(jwtTokenLifetime.Seconds()),
		"user_id":      userID,
		"role":         role,
	})
}
