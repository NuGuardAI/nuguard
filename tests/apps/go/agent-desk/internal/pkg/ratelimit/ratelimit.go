// Package ratelimit provides a small in-process fixed-window counter for the
// public endpoints that an unauthenticated caller can drive.
//
// It is deliberately not distributed. A deployment running several replicas gets
// the configured limit per replica rather than per cluster, which weakens the
// bound by a factor of the replica count. That is the right trade here because
// these limits exist to make flooding expensive, not to meter a quota, and
// because reaching for a shared store would put a network round trip on the
// hottest unauthenticated paths in the application. If this ever needs to be
// exact across replicas, the Limiter interface is small enough to swap.
package ratelimit

import (
	"sync"
	"time"
)

type bucket struct {
	count   int
	resetAt time.Time
}

// Limiter counts requests per key inside a fixed window. The zero value is not
// usable; call New. A nil *Limiter allows everything, so a caller can represent
// "disabled" by holding nil instead of branching at every use site.
type Limiter struct {
	limit  int
	window time.Duration

	mu      sync.Mutex
	buckets map[string]bucket
	sweptAt time.Time
}

// New returns a Limiter that allows limit requests per window for each distinct
// key. A limit of zero or less, or a non-positive window, disables the limiter.
func New(limit int, window time.Duration) *Limiter {
	if limit <= 0 || window <= 0 {
		return &Limiter{limit: limit, window: window, buckets: make(map[string]bucket)}
	}
	return &Limiter{limit: limit, window: window, buckets: make(map[string]bucket)}
}

// Allow records one request for key and reports whether it may proceed. When it
// returns false, retryAfter is how long the caller should wait before the window
// resets, and is meant for a Retry-After header.
func (l *Limiter) Allow(key string) (bool, time.Duration) {
	if l == nil || l.limit <= 0 || l.window <= 0 {
		return true, 0
	}

	now := time.Now()

	l.mu.Lock()
	defer l.mu.Unlock()

	l.sweep(now)

	current, ok := l.buckets[key]
	if !ok || !now.Before(current.resetAt) {
		l.buckets[key] = bucket{count: 1, resetAt: now.Add(l.window)}
		return true, 0
	}

	current.count++
	l.buckets[key] = current
	if current.count > l.limit {
		retryAfter := current.resetAt.Sub(now)
		if retryAfter < 0 {
			retryAfter = 0
		}
		return false, retryAfter
	}
	return true, 0
}

// Len reports how many keys are currently tracked. It exists for tests.
func (l *Limiter) Len() int {
	if l == nil {
		return 0
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	return len(l.buckets)
}

// sweep drops buckets whose window has elapsed, so a long-running process does
// not accumulate one entry per address it has ever seen. It runs at most once per
// window and under the same lock as Allow, which keeps the cost amortised and
// avoids a background goroutine whose lifetime somebody would have to own.
func (l *Limiter) sweep(now time.Time) {
	if !l.sweptAt.IsZero() && now.Sub(l.sweptAt) < l.window {
		return
	}
	for key, current := range l.buckets {
		if !now.Before(current.resetAt) {
			delete(l.buckets, key)
		}
	}
	l.sweptAt = now
}
