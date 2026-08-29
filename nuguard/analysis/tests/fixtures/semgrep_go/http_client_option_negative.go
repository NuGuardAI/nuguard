package semgrepfixtures

import (
	"context"
	"crypto/tls"
	"net/http"

	"google.golang.org/genai"
)

// Secure or AI-unrelated option.WithHTTPClient flows must not be reported (#232).

func SecureTLSViaWithHTTPClient() {
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{
			InsecureSkipVerify: false,
		},
	}
	httpClient := &http.Client{Transport: transport}
	client := genai.NewClient(
		context.Background(),
		nil,
		option.WithHTTPClient(httpClient),
	)
	_ = client
}

// InsecureSkipVerify on a client that never reaches an AI provider is out of scope.
func InsecureTLSOnNonAIHTTPClient() {
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{
			InsecureSkipVerify: true,
		},
	}
	httpClient := &http.Client{Transport: transport}
	resp, err := httpClient.Get("https://internal.example.com")
	if err != nil {
		return
	}
	defer resp.Body.Close()
}
