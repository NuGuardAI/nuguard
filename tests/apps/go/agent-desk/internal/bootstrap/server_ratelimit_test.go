package bootstrap

import (
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"agent-desk/internal/pkg/config"

	"github.com/gin-gonic/gin"
)

func parseInt(t *testing.T, value string) int {
	t.Helper()
	parsed, err := strconv.Atoi(value)
	if err != nil {
		t.Fatalf("%q is not an integer", value)
	}
	return parsed
}

// setRateLimitTestConfig disables password login so /api/auth/login returns
// before it reaches the database. The limiter runs ahead of the handler either
// way, which is what these tests are about.
func setRateLimitTestConfig(rateLimit config.RateLimitConfig) {
	disabled := false
	config.SetCurrent(&config.Config{
		Server: config.ServerConfig{
			RateLimit: rateLimit,
			CORS:      config.CORSConfig{AllowedOrigins: []string{}},
		},
		Auth:    config.AuthConfig{PasswordLoginEnabled: &disabled},
		Storage: config.StorageConfig{Local: config.LocalStorageConfig{Root: "storage", BaseURL: "/storage"}},
	})
}

func postJSON(app *gin.Engine, path, clientIP string) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(`{"username":"admin","password":"secret"}`))
	req.Header.Set("Content-Type", "application/json")
	req.RemoteAddr = clientIP + ":52000"
	app.ServeHTTP(rec, req)
	return rec
}

func TestNewServerRateLimitsThePublicLoginEndpoint(t *testing.T) {
	setRateLimitTestConfig(config.RateLimitConfig{})
	app, err := NewServer()
	if err != nil {
		t.Fatalf("NewServer() error = %v", err)
	}

	for i := 1; i <= limitLogin; i++ {
		if rec := postJSON(app, "/api/auth/login", "203.0.113.7"); rec.Code == http.StatusTooManyRequests {
			t.Fatalf("request %d was throttled although the login limit is %d per window", i, limitLogin)
		}
	}
	rec := postJSON(app, "/api/auth/login", "203.0.113.7")
	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("request %d got status %d, want 429", limitLogin+1, rec.Code)
	}
	if rec.Header().Get("Retry-After") == "" {
		t.Error("the 429 carried no Retry-After header")
	}

	// A second address must keep working, otherwise one attacker could lock the
	// login page for every user behind their own NAT.
	if rec := postJSON(app, "/api/auth/login", "198.51.100.9"); rec.Code == http.StatusTooManyRequests {
		t.Fatal("an unrelated address was throttled by another address's requests")
	}
}

func TestNewServerRateLimitsTheSupportRegistrationEndpoint(t *testing.T) {
	setRateLimitTestConfig(config.RateLimitConfig{})
	app, err := NewServer()
	if err != nil {
		t.Fatalf("NewServer() error = %v", err)
	}

	for i := 1; i <= limitSupportRegister; i++ {
		postJSON(app, "/api/support/auth/register", "203.0.113.7")
	}
	if rec := postJSON(app, "/api/support/auth/register", "203.0.113.7"); rec.Code != http.StatusTooManyRequests {
		t.Fatalf("registration attempt %d got status %d, want 429", limitSupportRegister+1, rec.Code)
	}
}

// TestNewServerLeavesWebhooksAndPublicReadsUnthrottled guards the exemption that
// matters most. A channel platform that receives a 429 from its webhook stops
// retrying and eventually disables the delivery, which takes a whole channel
// offline - a far worse outcome than the flood the limit was meant to stop.
func TestNewServerLeavesWebhooksAndPublicReadsUnthrottled(t *testing.T) {
	setRateLimitTestConfig(config.RateLimitConfig{})
	app, err := NewServer()
	if err != nil {
		t.Fatalf("NewServer() error = %v", err)
	}

	requests := []struct {
		method string
		path   string
	}{
		{http.MethodGet, "/api/health"},
		{http.MethodGet, "/api/config"},
		{http.MethodPost, "/api/webhooks/org-sync"},
		{http.MethodGet, "/api/third/telegram/webhook"},
		{http.MethodPost, "/api/third/whatsapp/webhook"},
	}
	for _, r := range requests {
		for i := 0; i < 200; i++ {
			rec := httptest.NewRecorder()
			req := httptest.NewRequest(r.method, r.path, strings.NewReader(`{}`))
			req.Header.Set("Content-Type", "application/json")
			req.RemoteAddr = "203.0.113.7:52000"
			app.ServeHTTP(rec, req)
			// Any status is acceptable here except 429: these routes either
			// succeed, fail validation, or error out, but they must never be
			// throttled.
			if rec.Code == http.StatusTooManyRequests {
				t.Fatalf("%s %s returned 429 on request %d; this route must stay unthrottled", r.method, r.path, i+1)
			}
		}
	}
}

func TestNewServerRateLimitCanBeDisabled(t *testing.T) {
	disabled := false
	setRateLimitTestConfig(config.RateLimitConfig{Enabled: &disabled})
	app, err := NewServer()
	if err != nil {
		t.Fatalf("NewServer() error = %v", err)
	}

	for i := 0; i < limitLogin*4; i++ {
		if rec := postJSON(app, "/api/auth/login", "203.0.113.7"); rec.Code == http.StatusTooManyRequests {
			t.Fatalf("request %d was throttled although rate limiting is disabled", i+1)
		}
	}
}

func TestNewServerRateLimitWindowIsConfigurable(t *testing.T) {
	setRateLimitTestConfig(config.RateLimitConfig{WindowSeconds: 3600})
	app, err := NewServer()
	if err != nil {
		t.Fatalf("NewServer() error = %v", err)
	}

	for i := 1; i <= limitLogin; i++ {
		postJSON(app, "/api/auth/login", "203.0.113.7")
	}
	rec := postJSON(app, "/api/auth/login", "203.0.113.7")
	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("got status %d, want 429", rec.Code)
	}
	if retryAfter := rec.Header().Get("Retry-After"); retryAfter == "" {
		t.Fatal("no Retry-After header")
	} else if seconds := parseInt(t, retryAfter); seconds < 3500 || seconds > 3600 {
		t.Errorf("Retry-After = %s, want roughly the configured 3600 second window", retryAfter)
	}
}
