package agent

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
)

func TestNewOpenAICompatibleModelRejectsMissingCredentials(t *testing.T) {
	_, err := NewOpenAICompatibleModel(context.Background(), OpenAIModelConfig{
		BaseURL: "https://example.invalid/v1", Model: "demo", Timeout: 30 * time.Second,
	})

	assert.ErrorIs(t, err, ErrInvalidModelConfig)
}
