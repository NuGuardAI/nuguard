package semgrepfixtures

import (
	"github.com/sashabaranov/go-openai"
)

// providerConfig is a minimal local type that preserves provider-neutral
// credential field names without pretending go-openai.ClientConfig exposes them.
type providerConfig struct {
	AuthToken string
	APIKey    string
	ApiKey    string
}

func HardcodedOpenAIDefaultConfig() {
	cfg := openai.DefaultConfig("sk-SYNTHETIC_TEST_KEY_001")
	_ = openai.NewClientWithConfig(cfg)
}

func HardcodedAuthTokenAssignment() {
	cfg := providerConfig{}
	cfg.AuthToken = "sk-SYNTHETIC_TEST_KEY_002"
	_ = cfg
}

func HardcodedAPIKeyField() {
	cfg := providerConfig{}
	cfg.APIKey = "AIzaSYNTHETIC_TEST_KEY_003"
	_ = cfg
}

func HardcodedApiKeyField() {
	cfg := providerConfig{}
	cfg.ApiKey = "sk-SYNTHETIC_TEST_KEY_004"
	_ = cfg
}
