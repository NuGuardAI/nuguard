package middleware

import (
	"net/http"
	"strconv"
	"time"

	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/pkg/ratelimit"

	"github.com/gin-gonic/gin"
)

// rateLimitMessageKey is the localized explanation returned with a 429. It
// deliberately does not disclose the limit or the remaining budget, which would
// only tell a caller how much room they have left.
const rateLimitMessageKey = "error.e0354"

// RateLimit bounds how often one client address may call a single public
// endpoint.
//
// The address comes from ctx.ClientIP(), which is only meaningful because
// NewServer configures Gin's trusted proxies and platform before any middleware
// is registered. Without that a caller picks their own bucket by setting
// X-Forwarded-For, and the limit is decoration.
//
// A nil limiter allows everything, so "disabled" is expressed by handing out nil
// limiters rather than by branching at every call site.
//
// Rejections are not logged here. requestLogMiddleware already records the path,
// status and client address of every request, so a 429 is visible there without
// giving a flood a second way to fill the log.
func RateLimit(limiter *ratelimit.Limiter) gin.HandlerFunc {
	return func(ctx *gin.Context) {
		allowed, retryAfter := limiter.Allow(ctx.ClientIP())
		if allowed {
			ctx.Next()
			return
		}
		// Retry-After is a whole number of seconds, so round up: telling a caller
		// to come back sooner than the window actually resets just earns another
		// 429. Rounding down and adding one overshoots when the remaining time is
		// already an exact number of seconds.
		seconds := int64(retryAfter / time.Second)
		if time.Duration(seconds)*time.Second < retryAfter {
			seconds++
		}
		if seconds < 1 {
			seconds = 1
		}
		ctx.Header("Retry-After", strconv.FormatInt(seconds, 10))
		httpx.WriteHttpStatusJSON(ctx, http.StatusTooManyRequests, errorsx.InvalidParamI18n(rateLimitMessageKey))
		ctx.Abort()
	}
}
