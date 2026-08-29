package semgrepfixtures

import (
	"context"
	"crypto/tls"
	"net/http"

	"google.golang.org/genai"
)

// option.WithHTTPClient flows carrying an insecure TLS transport (#232).

func InsecureTLSViaWithHTTPClientVariable() {
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{
			InsecureSkipVerify: true,
		},
	}
	httpClient := &http.Client{Transport: transport}
	client := genai.NewClient(
		context.Background(),
		nil,
		option.WithHTTPClient(httpClient),
	)
	client.Models.GenerateContentStream(context.Background(), "gemini-2.0-flash", "hello", nil)
}

func InsecureTLSViaWithHTTPClientLiteral() {
	client := genai.NewClient(
		context.Background(),
		nil,
		option.WithHTTPClient(&http.Client{
			Transport: &http.Transport{
				TLSClientConfig: &tls.Config{
					InsecureSkipVerify: true,
				},
			},
		}),
	)
	client.Models.GenerateContent(context.Background(), "gemini-2.0-flash", "hello", nil)
}
