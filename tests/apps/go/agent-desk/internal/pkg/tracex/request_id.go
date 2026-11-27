package tracex

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"strings"
)

const (
	RequestIDHeader = "X-Request-Id"
	GinRequestIDKey = "requestId"
)

type requestIDContextKey struct{}

func NormalizeRequestID(value string) string {
	value = strings.TrimSpace(value)
	if value == "" || len(value) > 128 {
		return ""
	}
	for _, r := range value {
		if r < 33 || r > 126 {
			return ""
		}
	}
	return value
}

func EnsureRequestID(value string) string {
	if normalized := NormalizeRequestID(value); normalized != "" {
		return normalized
	}
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return ""
	}
	return hex.EncodeToString(b[:])
}

func ContextWithRequestID(ctx context.Context, requestID string) context.Context {
	requestID = NormalizeRequestID(requestID)
	if requestID == "" {
		return ctx
	}
	return context.WithValue(ctx, requestIDContextKey{}, requestID)
}

func RequestIDFromContext(ctx context.Context) string {
	if ctx == nil {
		return ""
	}
	if value, ok := ctx.Value(requestIDContextKey{}).(string); ok {
		return NormalizeRequestID(value)
	}
	return ""
}
