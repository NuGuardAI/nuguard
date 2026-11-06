package agent

import (
	"context"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/runtrace"
)

type Input struct {
	ConversationID uuid.UUID
	MessageID      uuid.UUID
	RunID          uuid.UUID
	CustomerID     uuid.UUID
	Text           string
}

type Engine interface {
	Run(context.Context, Input, runtrace.Sink) (domain.Draft, error)
}

type ToolFailure struct {
	Tool      string
	Code      string
	Retryable bool
}

type EvidenceState struct {
	CitationCount int
	FactCount     int
	ToolFailures  []ToolFailure
}
