package config

import (
	"fmt"
	"os"
	"strings"
)

const (
	ModeMock             = "mock"
	ModeOpenAICompatible = "openai-compatible"
)

type Config struct {
	AppMode       string
	HTTPAddr      string
	MCPAddr       string
	DatabaseURL   string
	MCPURL        string
	ModelBaseURL  string
	ModelAPIKey   string
	ModelName     string
	EmbeddingName string
}

func Load() (Config, error) {
	cfg := Config{
		AppMode:       envOr("APP_MODE", ModeMock),
		HTTPAddr:      envOr("HTTP_ADDR", ":8080"),
		MCPAddr:       envOr("MCP_ADDR", ":8090"),
		DatabaseURL:   envOr("DATABASE_URL", "postgres://copilot:copilot@localhost:5432/copilot?sslmode=disable"),
		MCPURL:        envOr("MCP_URL", "http://localhost:8090/mcp"),
		ModelBaseURL:  strings.TrimSpace(os.Getenv("MODEL_BASE_URL")),
		ModelAPIKey:   strings.TrimSpace(os.Getenv("MODEL_API_KEY")),
		ModelName:     envOr("MODEL_NAME", "gpt-4.1-mini"),
		EmbeddingName: envOr("EMBEDDING_MODEL", "text-embedding-3-small"),
	}

	if cfg.AppMode != ModeMock && cfg.AppMode != ModeOpenAICompatible {
		return Config{}, fmt.Errorf("APP_MODE must be mock or openai-compatible")
	}
	if cfg.AppMode == ModeOpenAICompatible && cfg.ModelAPIKey == "" {
		return Config{}, fmt.Errorf("MODEL_API_KEY is required when APP_MODE=openai-compatible")
	}

	return cfg, nil
}

func envOr(key, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}
