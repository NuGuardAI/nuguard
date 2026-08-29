package semgrepfixtures

import (
	"context"

	"google.golang.org/genai"
	"openai"
)

// Hardcoded credentials passed through official SDK client options (#232).

func HardcodedKeyViaWithAPIKey() {
	client, err := openai.NewClient(
		"https://api.example.com",
		option.WithAPIKey("sk-SYNTHETIC_TEST_KEY_005"),
	)
	if err != nil {
		return
	}
	_ = client
}

func HardcodedKeyViaWithAuthToken() {
	client := genai.NewClient(context.Background(), nil, option.WithAuthToken("sk-SYNTHETIC_TEST_KEY_006"))
	_ = client
}

func HardcodedGoogleKeyViaWithToken() {
	client := genai.NewClient(context.Background(), nil, option.WithToken("AIzaSYNTHETIC_TEST_KEY_007"))
	_ = client
}
