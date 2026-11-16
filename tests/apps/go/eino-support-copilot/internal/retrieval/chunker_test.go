package retrieval

import (
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestChunkerPreservesHeadingLocator(t *testing.T) {
	doc := Document{
		Filename: "guide.md",
		Text:     "# Billing\n\n## Activation\n\nPayment can take ten minutes.",
	}

	chunks, err := NewChunker(500, 0.15).Split(doc)

	require.NoError(t, err)
	require.Len(t, chunks, 1)
	assert.Equal(t, "Billing / Activation", chunks[0].Locator)
	assert.Contains(t, chunks[0].Content, "ten minutes")
}

func TestChunkerSplitsOversizedSectionAndKeepsBoundedOverlap(t *testing.T) {
	doc := Document{
		Filename: "long.md",
		Text:     "# Limits\n\n" + strings.Repeat("one two three four five. ", 8),
	}

	chunks, err := NewChunker(12, 0.15).Split(doc)

	require.NoError(t, err)
	require.Greater(t, len(chunks), 1)
	for _, chunk := range chunks {
		assert.LessOrEqual(t, tokenCount(chunk.Content), 12)
		assert.Equal(t, "Limits", chunk.Locator)
	}
}

func TestChunkerPrefersSentenceBoundaries(t *testing.T) {
	doc := Document{
		Filename: "sentences.md",
		Text: "# Timing\n\n" +
			"First sentence has five tokens. Second sentence has five tokens. Third sentence has five tokens.",
	}

	chunks, err := NewChunker(7, 0).Split(doc)

	require.NoError(t, err)
	require.Len(t, chunks, 3)
	for _, chunk := range chunks {
		assert.True(t, strings.HasSuffix(chunk.Content, "."), chunk.Content)
	}
}
