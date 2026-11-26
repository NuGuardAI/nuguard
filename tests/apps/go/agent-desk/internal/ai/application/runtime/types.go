package runtime

import (
	"agent-desk/internal/models"
	"time"
)

// RunInput is the normalized, fully prepared input for the Agent Loop.
type RunInput struct {
	Conversation models.Conversation
	UserMessage  models.Message
	AIAgent      models.AIAgent
	AIConfig     models.AIConfig
	CheckPointID string
	Debug        bool
}

// ResumeInput extends the prepared input with an approved interrupt payload.
type ResumeInput struct {
	Conversation models.Conversation
	UserMessage  models.Message
	AIAgent      models.AIAgent
	AIConfig     models.AIConfig
	CheckPointID string
	ResumeData   map[string]string
	Debug        bool
}

type InterruptContextSummary struct {
	Type        string `json:"type,omitempty"`
	ID          string `json:"id"`
	DisplayName string `json:"displayName,omitempty"`
	PromptText  string `json:"promptText,omitempty"`
	InfoPreview string `json:"infoPreview,omitempty"`
}

// RunResult is the normalized Agent Loop result.
type RunResult struct {
	RunID                 string
	Status                string
	ReplyText             string
	PlannedSkillID        int64
	PlannedSkillName      string
	SkillAllowedToolCodes []string
	ModelName             string
	PromptTokens          int
	CompletionTokens      int
	HistoryMessageCount   int
	RetrieverCount        int
	ToolCallCount         int
	InvokedToolCodes      []string
	WorkflowID            int64
	WorkflowVersionID     int64
	WorkflowRunID         int64
	AgentRunID            int64
	WorkflowNodePath      []string
	CheckPointID          string
	CheckPointData        string
	Interrupted           bool
	HandoffRequested      bool
	HandoffReason         string
	ConversationDecision  *ConversationDecision
	Interrupts            []InterruptContextSummary
	TraceData             string
	ErrorMessage          string
}

type ConversationAction string

const (
	ConversationActionReply                  ConversationAction = "reply"
	ConversationActionHandoff                ConversationAction = "handoff"
	ConversationActionAskHandoffConfirmation ConversationAction = "ask_handoff_confirmation"
)

type HandoffInitiator string

const (
	HandoffInitiatorNone     HandoffInitiator = "none"
	HandoffInitiatorCustomer HandoffInitiator = "customer"
	HandoffInitiatorAgent    HandoffInitiator = "agent"
)

// ConversationDecision is the model's structured, non-side-effect decision.
// The runtime remains the only component that can execute a handoff.
type ConversationDecision struct {
	Action           ConversationAction `json:"action"`
	Reason           string             `json:"reason"`
	Reply            string             `json:"reply"`
	HandoffInitiator HandoffInitiator   `json:"handoffInitiator"`
	HandoffConfirmed bool               `json:"handoffConfirmed"`
}

type StreamEventType string

const (
	StreamEventStarted   StreamEventType = "started"
	StreamEventStep      StreamEventType = "step"
	StreamEventOutput    StreamEventType = "output"
	StreamEventCompleted StreamEventType = "completed"
	StreamEventFailed    StreamEventType = "failed"
)

// StreamEvent is the transport-neutral event contract for future streaming.
type StreamEvent struct {
	Type       StreamEventType `json:"type"`
	RunID      string          `json:"runId,omitempty"`
	AgentRunID int64           `json:"agentRunId,omitempty"`
	StepCode   string          `json:"stepCode,omitempty"`
	Content    string          `json:"content,omitempty"`
	Error      string          `json:"error,omitempty"`
	OccurredAt time.Time       `json:"occurredAt"`
}
