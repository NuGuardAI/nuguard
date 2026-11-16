package agent

import "errors"

var (
	ErrInvalidInput       = errors.New("invalid agent input")
	ErrInvalidGuardConfig = errors.New("invalid guard configuration")
	ErrEventSink          = errors.New("agent event sink failed")
	ErrOutputValidation   = errors.New("model output validation failed")
	ErrInvalidModelConfig = errors.New("invalid OpenAI-compatible model configuration")
)

const (
	ReasonSchemaInvalid      = "SCHEMA_INVALID"
	ReasonHighPriority       = "HIGH_PRIORITY"
	ReasonLowConfidence      = "LOW_CONFIDENCE"
	ReasonMissingCitation    = "MISSING_CITATION"
	ReasonMissingFact        = "MISSING_FACT"
	ReasonToolPartialFailure = "TOOL_PARTIAL_FAILURE"
)
