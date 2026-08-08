// NuGuard minimal official anthropic-sdk-go call-shape fixture (issue #223).
// Independently written structural sample — not vendored upstream source.
// See ../ATTRIBUTION.md.
package claude_sample

import (
	"context"
	"fmt"
)

type claudeMessagesService struct{}

func (claudeMessagesService) New(ctx context.Context, req any, _ ...any) {}

type claudeOfficialClient struct {
	Messages claudeMessagesService
}

// OfficialMessagesWithUserPrompt exercises Messages.New.
func OfficialMessagesWithUserPrompt(userInput string) {
	client := claudeOfficialClient{}
	prompt := fmt.Sprintf("Discuss: %s", userInput)
	client.Messages.New(context.Background(), prompt)
}
