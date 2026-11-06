package config

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestLoadUsesSafeMockDefaults(t *testing.T) {
	t.Setenv("APP_MODE", "")
	t.Setenv("HTTP_ADDR", "")
	t.Setenv("MCP_ADDR", "")
	t.Setenv("MODEL_API_KEY", "")

	cfg, err := Load()

	require.NoError(t, err)
	assert.Equal(t, "mock", cfg.AppMode)
	assert.Equal(t, ":8080", cfg.HTTPAddr)
	assert.Equal(t, ":8090", cfg.MCPAddr)
	assert.Equal(t, "http://localhost:8090/mcp", cfg.MCPURL)
	assert.Empty(t, cfg.ModelAPIKey)
}

func TestLoadRejectsUnknownMode(t *testing.T) {
	t.Setenv("APP_MODE", "autonomous")

	_, err := Load()

	require.ErrorContains(t, err, "APP_MODE must be mock or openai-compatible")
}

func TestLoadRequiresAPIKeyForRealModelMode(t *testing.T) {
	t.Setenv("APP_MODE", "openai-compatible")
	t.Setenv("MODEL_API_KEY", "")

	_, err := Load()

	require.ErrorContains(t, err, "MODEL_API_KEY is required")
}
