package semgrepfixtures

import (
	"context"
	"os"

	"google.golang.org/genai"
	"openai"
)

// Credential options sourced from the environment or holding non-secret
// literals must not be reported (#232).

func EnvSourcedCredentialOptions() {
	client, err := openai.NewClient(
		"https://api.example.com",
		option.WithAPIKey(os.Getenv("OPENAI_API_KEY")),
	)
	if err != nil {
		return
	}
	_ = client
}

func EnvSourcedAuthToken() {
	client := genai.NewClient(context.Background(), nil, option.WithAuthToken(os.Getenv("GEMINI_API_KEY")))
	_ = client
}

// Non-credential literals do not match the key-prefix gate.
func UnrelatedOptionLiteral() {
	opts := option.WithEndpoint("https://api.example.com")
	_ = opts
}
