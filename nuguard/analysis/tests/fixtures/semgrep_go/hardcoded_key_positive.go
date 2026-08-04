package semgrepfixtures

import (
	"github.com/sashabaranov/go-openai"
)

func HardcodedOpenAIDefaultConfig() {
	cfg := openai.DefaultConfig("sk-SYNTHETIC_TEST_KEY_001")
	_ = openai.NewClientWithConfig(*cfg)
}

func HardcodedAuthTokenAssignment() {
	cfg := openai.DefaultConfig("")
	cfg.AuthToken = "sk-SYNTHETIC_TEST_KEY_002"
	_ = openai.NewClientWithConfig(*cfg)
}

func HardcodedAPIKeyField() {
	cfg := openai.DefaultConfig("")
	cfg.APIKey = "AIzaSYNTHETIC_TEST_KEY_003"
	_ = openai.NewClientWithConfig(*cfg)
}
