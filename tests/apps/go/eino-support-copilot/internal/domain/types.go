package domain

import (
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

type MessageRole string

const (
	RoleUser      MessageRole = "user"
	RoleAssistant MessageRole = "assistant"
	RoleAgent     MessageRole = "agent"
)

type Message struct {
	ID             uuid.UUID       `json:"id"`
	ConversationID uuid.UUID       `json:"conversation_id"`
	Role           MessageRole     `json:"role"`
	Content        string          `json:"content"`
	Metadata       json.RawMessage `json:"metadata"`
	CreatedAt      time.Time       `json:"created_at"`
}

type Conversation struct {
	ID         uuid.UUID `json:"id"`
	CustomerID uuid.UUID `json:"customer_id"`
	Title      string    `json:"title"`
	CreatedAt  time.Time `json:"created_at"`
	UpdatedAt  time.Time `json:"updated_at"`
	Messages   []Message `json:"messages"`
}

type Priority string

const (
	PriorityP0 Priority = "P0"
	PriorityP1 Priority = "P1"
	PriorityP2 Priority = "P2"
	PriorityP3 Priority = "P3"
)

type Citation struct {
	ChunkID uuid.UUID `json:"chunk_id"`
	Title   string    `json:"title"`
	Locator string    `json:"locator"`
	Content string    `json:"content"`
	Score   float64   `json:"score,omitempty"`
}

type KnowledgeStatus string

const (
	KnowledgeUploaded KnowledgeStatus = "uploaded"
	KnowledgeIndexing KnowledgeStatus = "indexing"
	KnowledgeReady    KnowledgeStatus = "ready"
	KnowledgeFailed   KnowledgeStatus = "failed"
)

type KnowledgeDocument struct {
	ID           uuid.UUID       `json:"id"`
	Filename     string          `json:"filename"`
	MediaType    string          `json:"media_type"`
	SHA256       string          `json:"sha256"`
	Status       KnowledgeStatus `json:"status"`
	ErrorMessage *string         `json:"error_message,omitempty"`
	CreatedAt    time.Time       `json:"created_at"`
	UpdatedAt    time.Time       `json:"updated_at"`
}

type Fact struct {
	Source string `json:"source"`
	Field  string `json:"field"`
	Value  any    `json:"value"`
}

type Draft struct {
	Intent           string     `json:"intent"`
	Priority         Priority   `json:"priority"`
	Answer           string     `json:"answer"`
	Citations        []Citation `json:"citations"`
	Facts            []Fact     `json:"facts"`
	Confidence       float64    `json:"confidence"`
	RequiresReview   bool       `json:"requires_review"`
	ReviewReasons    []string   `json:"review_reasons"`
	FollowUpQuestion *string    `json:"follow_up_question"`
}

type TicketStatus string

const (
	TicketOpen          TicketStatus = "open"
	TicketTriaged       TicketStatus = "triaged"
	TicketWaitingReview TicketStatus = "waiting_review"
	TicketResolved      TicketStatus = "resolved"
	TicketEscalated     TicketStatus = "escalated"
)

type ReviewAction string

const (
	ReviewApprove        ReviewAction = "approve"
	ReviewEditAndApprove ReviewAction = "edit_and_approve"
	ReviewEscalate       ReviewAction = "escalate"
)

type TicketReview struct {
	ID          uuid.UUID    `json:"id"`
	TicketID    uuid.UUID    `json:"ticket_id"`
	ReviewerID  uuid.UUID    `json:"reviewer_id"`
	Action      ReviewAction `json:"action"`
	FinalAnswer *string      `json:"final_answer"`
	CreatedAt   time.Time    `json:"created_at"`
}

type Ticket struct {
	ID             uuid.UUID      `json:"id"`
	ConversationID uuid.UUID      `json:"conversation_id"`
	Status         TicketStatus   `json:"status"`
	Priority       Priority       `json:"priority"`
	Intent         string         `json:"intent"`
	Summary        string         `json:"summary"`
	Draft          *Draft         `json:"draft"`
	CreatedAt      time.Time      `json:"created_at"`
	UpdatedAt      time.Time      `json:"updated_at"`
	Reviews        []TicketReview `json:"reviews"`
}

type RunStatus string

const (
	RunQueued        RunStatus = "queued"
	RunRunning       RunStatus = "running"
	RunWaitingReview RunStatus = "waiting_review"
	RunCompleted     RunStatus = "completed"
	RunFailed        RunStatus = "failed"
	RunCancelled     RunStatus = "cancelled"
)

type AgentRun struct {
	ID                uuid.UUID       `json:"id"`
	ConversationID    uuid.UUID       `json:"conversation_id"`
	MessageID         *uuid.UUID      `json:"message_id"`
	TicketID          *uuid.UUID      `json:"ticket_id"`
	Status            RunStatus       `json:"status"`
	Mode              string          `json:"mode"`
	ModelName         *string         `json:"model_name"`
	InputSummary      string          `json:"input_summary"`
	OutputSummary     *string         `json:"output_summary"`
	Usage             json.RawMessage `json:"usage"`
	ErrorCode         *string         `json:"error_code"`
	ErrorMessage      *string         `json:"error_message"`
	ReplayedFromRunID *uuid.UUID      `json:"replayed_from_run_id"`
	StartedAt         *time.Time      `json:"started_at"`
	CompletedAt       *time.Time      `json:"completed_at"`
	CreatedAt         time.Time       `json:"created_at"`
	Steps             []AgentRunStep  `json:"steps"`
}

type AgentRunStep struct {
	ID            uuid.UUID       `json:"id"`
	RunID         uuid.UUID       `json:"run_id"`
	Sequence      int             `json:"sequence"`
	EventType     EventType       `json:"event_type"`
	Stage         *string         `json:"stage"`
	InputSummary  json.RawMessage `json:"input_summary"`
	ToolName      *string         `json:"tool_name"`
	ToolArguments json.RawMessage `json:"tool_arguments"`
	ResultSummary json.RawMessage `json:"result_summary"`
	Citations     json.RawMessage `json:"citations"`
	DurationMS    *int64          `json:"duration_ms"`
	ErrorCode     *string         `json:"error_code"`
	CreatedAt     time.Time       `json:"created_at"`
}

type EventType string

const (
	EventRunStarted         EventType = "run.started"
	EventStageStarted       EventType = "stage.started"
	EventStageCompleted     EventType = "stage.completed"
	EventRetrievalCompleted EventType = "retrieval.completed"
	EventToolStarted        EventType = "tool.started"
	EventToolCompleted      EventType = "tool.completed"
	EventToolFailed         EventType = "tool.failed"
	EventDraftDelta         EventType = "draft.delta"
	EventCitation           EventType = "citation"
	EventReviewRequired     EventType = "review.required"
	EventRunCompleted       EventType = "run.completed"
	EventRunFailed          EventType = "run.failed"
)

type AgentEvent struct {
	EventID        uuid.UUID       `json:"event_id"`
	ConversationID uuid.UUID       `json:"conversation_id"`
	MessageID      uuid.UUID       `json:"message_id"`
	RunID          uuid.UUID       `json:"run_id"`
	Type           EventType       `json:"type"`
	Timestamp      time.Time       `json:"timestamp"`
	Payload        json.RawMessage `json:"payload"`
}
