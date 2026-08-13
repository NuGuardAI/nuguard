package semgrepfixtures

import (
	"context"
	"fmt"
)

// Bare .New(ctx, ...) must not become an LLM sink (issue #223).

type arbitraryStore struct{}

func (arbitraryStore) New(ctx context.Context, name string, _ ...any) {}

func BareNewNotLLMSink(userInput string) {
	store := arbitraryStore{}
	name := fmt.Sprintf("order-%s", userInput)
	store.New(context.Background(), name)
}

func BareNewMissingTimeoutNotLLM() {
	store := arbitraryStore{}
	store.New(context.Background(), "widget")
}
