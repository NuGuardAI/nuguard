package ratelimit

import (
	"fmt"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestLimiterAllowsUpToTheLimitThenRefuses(t *testing.T) {
	l := New(3, time.Minute)
	for i := 1; i <= 3; i++ {
		if ok, retryAfter := l.Allow("203.0.113.7"); !ok {
			t.Fatalf("request %d was refused although the limit is 3 (retryAfter=%v)", i, retryAfter)
		}
	}

	ok, retryAfter := l.Allow("203.0.113.7")
	if ok {
		t.Fatal("the fourth request was allowed although the limit is 3")
	}
	if retryAfter <= 0 || retryAfter > time.Minute {
		t.Fatalf("retryAfter = %v, want a positive duration within the window", retryAfter)
	}
}

func TestLimiterKeysAreIndependent(t *testing.T) {
	l := New(1, time.Minute)
	if ok, _ := l.Allow("203.0.113.7"); !ok {
		t.Fatal("the first request for a key was refused")
	}
	if ok, _ := l.Allow("203.0.113.7"); ok {
		t.Fatal("the second request for the same key was allowed")
	}
	// A different caller must not inherit somebody else's exhaustion. Without key
	// isolation one blocked address would lock out every user behind it.
	if ok, _ := l.Allow("198.51.100.9"); !ok {
		t.Fatal("a different key was refused because another key was exhausted")
	}
}

func TestLimiterWindowResets(t *testing.T) {
	l := New(1, 20*time.Millisecond)
	if ok, _ := l.Allow("k"); !ok {
		t.Fatal("the first request was refused")
	}
	if ok, _ := l.Allow("k"); ok {
		t.Fatal("the second request inside the window was allowed")
	}
	time.Sleep(40 * time.Millisecond)
	if ok, _ := l.Allow("k"); !ok {
		t.Fatal("the window did not reset")
	}
}

func TestNilLimiterAllowsEverything(t *testing.T) {
	var l *Limiter
	for i := 0; i < 100; i++ {
		if ok, retryAfter := l.Allow("k"); !ok {
			t.Fatalf("a nil limiter refused request %d (retryAfter=%v)", i, retryAfter)
		}
	}
	if l.Len() != 0 {
		t.Fatalf("Len() on a nil limiter = %d want 0", l.Len())
	}
}

func TestDisabledLimiterAllowsEverything(t *testing.T) {
	cases := []struct {
		name    string
		limiter *Limiter
	}{
		{"zero limit", New(0, time.Minute)},
		{"negative limit", New(-1, time.Minute)},
		{"zero window", New(5, 0)},
		{"negative window", New(5, -time.Second)},
	}
	for _, tc := range cases {
		for i := 0; i < 50; i++ {
			if ok, _ := tc.limiter.Allow("k"); !ok {
				t.Fatalf("%s: request %d was refused although the limiter is disabled", tc.name, i)
			}
		}
	}
}

func TestSweepReleasesExpiredKeys(t *testing.T) {
	l := New(10, 20*time.Millisecond)
	for i := 0; i < 200; i++ {
		l.Allow(fmt.Sprintf("key-%d", i))
	}
	if got := l.Len(); got != 200 {
		t.Fatalf("Len() = %d before the sweep, want 200", got)
	}

	time.Sleep(40 * time.Millisecond)
	// The sweep is driven by Allow rather than by a goroutine, so one more call
	// has to reclaim everything that expired.
	l.Allow("fresh")
	if got := l.Len(); got != 1 {
		t.Fatalf("Len() = %d after the sweep, want only the fresh key", got)
	}
}

// TestLimiterCountsExactlyUnderConcurrency is the reason Allow holds a single
// mutex across the read-modify-write rather than checking and then recording.
func TestLimiterCountsExactlyUnderConcurrency(t *testing.T) {
	const goroutines = 16
	const perGoroutine = 200
	const limit = 1000

	l := New(limit, time.Minute)
	var allowed atomic.Int64
	var wg sync.WaitGroup
	for i := 0; i < goroutines; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < perGoroutine; j++ {
				if ok, _ := l.Allow("shared"); ok {
					allowed.Add(1)
				}
			}
		}()
	}
	wg.Wait()

	if got := allowed.Load(); got != limit {
		t.Fatalf("allowed %d of %d requests, want exactly the limit of %d", got, goroutines*perGoroutine, limit)
	}
}
