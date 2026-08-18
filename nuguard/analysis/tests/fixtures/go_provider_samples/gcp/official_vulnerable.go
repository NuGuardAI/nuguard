// NuGuard minimal official google.golang.org/genai call-shape fixture (#223).
// Independently written structural sample — not vendored upstream source.
// See ../ATTRIBUTION.md.
package gcp_sample

import (
	"context"
	"fmt"
)

type geminiModelsService struct{}

func (geminiModelsService) GenerateContent(ctx context.Context, model string, contents any, config any, _ ...any) {
}

type geminiOfficialClient struct {
	Models geminiModelsService
}

// OfficialGenerateContentWithUserPrompt exercises Models.GenerateContent.
func OfficialGenerateContentWithUserPrompt(userInput string) {
	client := geminiOfficialClient{}
	prompt := fmt.Sprintf("Summarize: %s", userInput)
	client.Models.GenerateContent(context.Background(), "gemini-2.0-flash", prompt, nil)
}
