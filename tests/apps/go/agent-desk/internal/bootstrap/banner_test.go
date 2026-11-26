package bootstrap

import (
	"strings"
	"testing"

	"agent-desk/internal/pkg/config"
)

func TestRenderBanner(t *testing.T) {
	got := renderBanner(config.Config{
		Server: config.ServerConfig{Port: 8083},
		DB:     config.DBConfig{Type: "sqlite"},
	})

	expected := []string{
		":: AGENT DESK ::",
		"Port     : 8083",
		"DB       : sqlite",
		"Address  : http://127.0.0.1:8083",
	}
	for _, item := range expected {
		if !strings.Contains(got, item) {
			t.Fatalf("renderBanner() missing %q in:\n%s", item, got)
		}
	}
}

func TestRenderBannerDefaults(t *testing.T) {
	got := renderBanner(config.Config{})

	expected := []string{
		"Port     : 8080",
		"DB       : unknown",
		"Address  : http://127.0.0.1:8080",
	}
	for _, item := range expected {
		if !strings.Contains(got, item) {
			t.Fatalf("renderBanner() missing %q in:\n%s", item, got)
		}
	}
}
