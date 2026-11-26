package middleware

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
	"time"

	"agent-desk/internal/pkg/ratelimit"

	"github.com/gin-gonic/gin"
)

func newRateLimitTestEngine(limiter *ratelimit.Limiter) *gin.Engine {
	gin.SetMode(gin.TestMode)
	app := gin.New()
	app.POST("/probe", RateLimit(limiter), func(ctx *gin.Context) {
		ctx.JSON(http.StatusOK, gin.H{"success": true})
	})
	return app
}

func postProbe(app *gin.Engine, clientIP string) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/probe", nil)
	req.RemoteAddr = clientIP + ":52000"
	app.ServeHTTP(rec, req)
	return rec
}

func TestRateLimitRefusesAfterTheLimitAndSetsRetryAfter(t *testing.T) {
	app := newRateLimitTestEngine(ratelimit.New(2, time.Minute))

	for i := 1; i <= 2; i++ {
		if rec := postProbe(app, "203.0.113.7"); rec.Code != http.StatusOK {
			t.Fatalf("request %d got status %d, want 200", i, rec.Code)
		}
	}

	rec := postProbe(app, "203.0.113.7")
	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("the third request got status %d, want 429", rec.Code)
	}

	retryAfter := rec.Header().Get("Retry-After")
	if retryAfter == "" {
		t.Fatal("a 429 without Retry-After gives the caller nothing to act on")
	}
	seconds, err := strconv.Atoi(retryAfter)
	if err != nil {
		t.Fatalf("Retry-After = %q is not a number of seconds", retryAfter)
	}
	// The window is one minute and the requests are microseconds apart, so the
	// answer is 60 give or take a clock tick. What must not happen is a value
	// above the window, which would tell the caller to wait longer than needed, or
	// zero, which would have them retry immediately and collect another 429.
	if seconds < 1 || seconds > 60 {
		t.Errorf("Retry-After = %d seconds, want between 1 and the 60 second window", seconds)
	}

	// The body has to stay a JsonResult, because web/lib/api/client.ts parses the
	// payload before it looks at response.ok and surfaces payload.message.
	var body struct {
		Success bool   `json:"success"`
		Message string `json:"message"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("429 body is not JSON: %v (%s)", err, rec.Body.String())
	}
	if body.Success {
		t.Error("429 body reported success=true")
	}
	if body.Message == "" {
		t.Error("429 body carried no message, so the caller would see a blank error")
	}
}

func TestRateLimitIsKeyedByClientAddress(t *testing.T) {
	app := newRateLimitTestEngine(ratelimit.New(1, time.Minute))

	if rec := postProbe(app, "203.0.113.7"); rec.Code != http.StatusOK {
		t.Fatalf("first request got %d, want 200", rec.Code)
	}
	if rec := postProbe(app, "203.0.113.7"); rec.Code != http.StatusTooManyRequests {
		t.Fatalf("second request from the same address got %d, want 429", rec.Code)
	}
	// Somebody else must not inherit the first caller's exhaustion. Behind a NAT
	// this is the difference between a working product and a shared lockout.
	if rec := postProbe(app, "198.51.100.9"); rec.Code != http.StatusOK {
		t.Fatalf("a different address got %d, want 200", rec.Code)
	}
}

func TestRateLimitWithNilLimiterAllowsEverything(t *testing.T) {
	app := newRateLimitTestEngine(nil)
	for i := 0; i < 50; i++ {
		if rec := postProbe(app, "203.0.113.7"); rec.Code != http.StatusOK {
			t.Fatalf("request %d got %d with a nil limiter, want 200", i, rec.Code)
		}
	}
}
