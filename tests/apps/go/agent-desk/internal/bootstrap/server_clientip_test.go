package bootstrap

import (
	"bytes"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"agent-desk/internal/pkg/config"

	"github.com/gin-gonic/gin"
)

// captureRequestLog swaps the slog default for one writing into buf, because
// requestLogMiddleware records the address Gin resolved. That is the only place
// the server exposes its ClientIP() decision, and it is the value that ends up in
// t_login_credential_log and t_user.last_login_ip.
func captureRequestLog(t *testing.T) *bytes.Buffer {
	t.Helper()
	var buf bytes.Buffer
	previous := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(previous) })
	return &buf
}

func setTestServerConfig(server config.ServerConfig) {
	config.SetCurrent(&config.Config{
		Server:  server,
		Storage: config.StorageConfig{Local: config.LocalStorageConfig{Root: "storage", BaseURL: "/storage"}},
	})
}

// TestGinDefaultTrustsEveryProxy pins the vulnerability the configuration above
// exists to close. A bare Gin engine - which is what NewServer used to build -
// trusts 0.0.0.0/0 and ::/0, so validateHeader walks X-Forwarded-For right to
// left, finds no untrusted proxy to stop at, and returns the leftmost value the
// caller chose. If this assertion ever starts failing, Gin's default has changed
// and the trusted-proxy configuration should be revisited rather than assumed
// necessary.
func TestGinDefaultTrustsEveryProxy(t *testing.T) {
	app := gin.New()
	var resolved string
	app.GET("/probe", func(ctx *gin.Context) { resolved = ctx.ClientIP() })

	req := httptest.NewRequest(http.MethodGet, "/probe", nil)
	req.RemoteAddr = "203.0.113.7:52000"
	req.Header.Set("X-Forwarded-For", "198.51.100.9")
	app.ServeHTTP(httptest.NewRecorder(), req)

	if resolved != "198.51.100.9" {
		t.Fatalf("a bare engine resolved ClientIP to %q, expected the forged 198.51.100.9", resolved)
	}
}

func TestNewServerIgnoresForgedForwardedForFromAnUntrustedPeer(t *testing.T) {
	buf := captureRequestLog(t)
	setTestServerConfig(config.ServerConfig{})

	app, err := NewServer()
	if err != nil {
		t.Fatalf("NewServer() error = %v", err)
	}

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/health", nil)
	// A public peer is outside every default trusted range, so nothing it claims
	// about the originating address may be believed.
	req.RemoteAddr = "203.0.113.7:52000"
	req.Header.Set("X-Forwarded-For", "198.51.100.9")
	req.Header.Set("X-Real-IP", "198.51.100.9")
	app.ServeHTTP(rec, req)

	logged := buf.String()
	if !strings.Contains(logged, "clientIp=203.0.113.7") {
		t.Fatalf("expected the real peer address to be logged, got: %s", logged)
	}
	if strings.Contains(logged, "198.51.100.9") {
		t.Fatalf("a forged forwarding header was trusted: %s", logged)
	}
}

func TestNewServerReadsForwardedForFromATrustedProxy(t *testing.T) {
	buf := captureRequestLog(t)
	setTestServerConfig(config.ServerConfig{TrustedProxies: []string{"10.0.0.0/8"}})

	app, err := NewServer()
	if err != nil {
		t.Fatalf("NewServer() error = %v", err)
	}

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/health", nil)
	req.RemoteAddr = "10.0.0.5:52000"
	// A hostile client prepends a value it chose; the proxy appends the address it
	// actually saw. Gin walks the list right to left and stops at the first
	// address that is not itself a trusted proxy, which is the appended one.
	req.Header.Set("X-Forwarded-For", "198.51.100.9, 203.0.113.7")
	app.ServeHTTP(rec, req)

	logged := buf.String()
	if !strings.Contains(logged, "clientIp=203.0.113.7") {
		t.Fatalf("expected the proxy-appended address, got: %s", logged)
	}
	if strings.Contains(logged, "198.51.100.9") {
		t.Fatalf("the client-prepended address was trusted: %s", logged)
	}
}

func TestNewServerPrefersTheConfiguredTrustedPlatformHeader(t *testing.T) {
	buf := captureRequestLog(t)
	setTestServerConfig(config.ServerConfig{TrustedPlatform: "cloudflare"})

	app, err := NewServer()
	if err != nil {
		t.Fatalf("NewServer() error = %v", err)
	}

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/health", nil)
	req.RemoteAddr = "10.0.0.5:52000"
	req.Header.Set("X-Forwarded-For", "198.51.100.9")
	// The edge overwrites this header rather than appending to it, which is what
	// makes it usable as an identity at all.
	req.Header.Set("CF-Connecting-IP", "203.0.113.7")
	app.ServeHTTP(rec, req)

	logged := buf.String()
	if !strings.Contains(logged, "clientIp=203.0.113.7") {
		t.Fatalf("expected the platform header to win, got: %s", logged)
	}
	if strings.Contains(logged, "198.51.100.9") {
		t.Fatalf("X-Forwarded-For was consulted despite a trusted platform: %s", logged)
	}
}

func TestNewServerRejectsAnInvalidTrustedProxyCIDR(t *testing.T) {
	captureRequestLog(t)
	setTestServerConfig(config.ServerConfig{TrustedProxies: []string{"10.0.0.0/8", "not-a-cidr"}})

	if _, err := NewServer(); err == nil {
		t.Fatal("NewServer() accepted an unparseable CIDR; a typo here would silently disable the trust boundary")
	} else if !strings.Contains(err.Error(), "server.trustedProxies") {
		t.Fatalf("NewServer() error = %v, expected it to name server.trustedProxies", err)
	}
}
