package semgrepfixtures

import (
	"os"

	"github.com/sashabaranov/go-openai"
)

type secretStore struct{}

func (s secretStore) Get(name string) string { return "from-store" }

func HardcodedKeyFromEnv() {
	cfg := openai.DefaultConfig(os.Getenv("AI_API_TOKEN"))
	_ = openai.NewClientWithConfig(cfg)
}

func HardcodedKeyFromLookupEnv() {
	token, ok := os.LookupEnv("AI_API_TOKEN")
	if !ok {
		return
	}
	cfg := providerConfig{}
	cfg.AuthToken = token
	_ = cfg
}

func HardcodedKeyFromSecretStore() {
	store := secretStore{}
	cfg := providerConfig{}
	cfg.APIKey = store.Get("openai-key")
	_ = cfg
}

func HardcodedKeyApiKeyFromEnv() {
	cfg := providerConfig{}
	cfg.ApiKey = os.Getenv("AI_API_TOKEN")
	_ = cfg
}

func HarmlessStringAssignment() {
	cfg := openai.DefaultConfig("")
	cfg.BaseURL = "https://api.example.com/v1"
	_ = openai.NewClientWithConfig(cfg)
}
