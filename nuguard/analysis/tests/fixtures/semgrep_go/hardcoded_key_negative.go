package semgrepfixtures

import (
	"os"

	"github.com/sashabaranov/go-openai"
)

type secretStore struct{}

func (s secretStore) Get(name string) string { return "from-store" }

func HardcodedKeyFromEnv() {
	cfg := openai.DefaultConfig(os.Getenv("AI_API_TOKEN"))
	_ = openai.NewClientWithConfig(*cfg)
}

func HardcodedKeyFromLookupEnv() {
	cfg := openai.DefaultConfig("")
	cfg.AuthToken = os.LookupEnv("AI_API_TOKEN")
	_ = openai.NewClientWithConfig(*cfg)
}

func HardcodedKeyFromSecretStore() {
	store := secretStore{}
	cfg := openai.DefaultConfig("")
	cfg.APIKey = store.Get("openai-key")
	_ = openai.NewClientWithConfig(*cfg)
}

func HarmlessStringAssignment() {
	cfg := openai.DefaultConfig("")
	cfg.BaseURL = "https://api.example.com/v1"
	_ = openai.NewClientWithConfig(*cfg)
}
