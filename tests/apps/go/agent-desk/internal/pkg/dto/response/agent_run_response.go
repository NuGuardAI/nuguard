package response

import "agent-desk/internal/pkg/enums"

type AgentRunResponse struct {
	ID               int64                            `json:"id"`
	ConversationID   int64                            `json:"conversationId"`
	AIAgentID        int64                            `json:"aiAgentId"`
	AgentRevisionID  int64                            `json:"agentRevisionId"`
	SourceMessageID  int64                            `json:"sourceMessageId"`
	WorkflowRunID    int64                            `json:"workflowRunId"`
	Status           string                           `json:"status"`
	PromptTokens     int                              `json:"promptTokens"`
	CompletionTokens int                              `json:"completionTokens"`
	StartedAt        string                           `json:"startedAt"`
	EndedAt          string                           `json:"endedAt"`
	DurationMS       int64                            `json:"durationMs"`
	ErrorMessage     string                           `json:"errorMessage"`
	TraceData        string                           `json:"traceData"`
	CreatedAt        string                           `json:"createdAt"`
	UpdatedAt        string                           `json:"updatedAt"`
	Steps            []AgentStepResponse              `json:"steps,omitempty"`
	ToolCalls        []AgentToolCallResponse          `json:"toolCalls,omitempty"`
	QualityFeedback  *AgentRunQualityFeedbackResponse `json:"qualityFeedback,omitempty"`
}

type AgentRunQualityFeedbackResponse struct {
	ID               int64                          `json:"id"`
	AgentRunID       int64                          `json:"agentRunId"`
	ResolutionStatus enums.AgentRunResolutionStatus `json:"resolutionStatus"`
	EvidenceStatus   enums.AgentRunEvidenceStatus   `json:"evidenceStatus"`
	Comment          string                         `json:"comment"`
	UpdateUserName   string                         `json:"updateUserName"`
	UpdatedAt        string                         `json:"updatedAt"`
}

type AgentStepResponse struct {
	ID            int64  `json:"id"`
	AgentRunID    int64  `json:"agentRunId"`
	WorkflowRunID int64  `json:"workflowRunId"`
	StepType      string `json:"stepType"`
	StepCode      string `json:"stepCode"`
	Status        string `json:"status"`
	InputPreview  string `json:"inputPreview"`
	OutputPreview string `json:"outputPreview"`
	ErrorMessage  string `json:"errorMessage"`
	StartedAt     string `json:"startedAt"`
	EndedAt       string `json:"endedAt"`
	DurationMS    int    `json:"durationMs"`
}

type AgentToolCallResponse struct {
	ID               int64  `json:"id"`
	AgentRunID       int64  `json:"agentRunId"`
	AgentStepID      int64  `json:"agentStepId"`
	ToolCode         string `json:"toolCode"`
	RiskLevel        string `json:"riskLevel"`
	RequireConfirm   bool   `json:"requireConfirm"`
	Status           string `json:"status"`
	ArgumentsPreview string `json:"argumentsPreview"`
	ResultPreview    string `json:"resultPreview"`
	ErrorMessage     string `json:"errorMessage"`
	DurationMS       int    `json:"durationMs"`
	CreatedAt        string `json:"createdAt"`
}
