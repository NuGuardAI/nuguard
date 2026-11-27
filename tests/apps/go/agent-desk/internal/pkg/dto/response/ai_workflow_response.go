package response

import (
	"agent-desk/internal/ai/workflow/dsl"
	workflowregistry "agent-desk/internal/ai/workflow/registry"
	workflowvalidator "agent-desk/internal/ai/workflow/validator"
	"agent-desk/internal/pkg/enums"
)

type AIWorkflowResponse struct {
	ID                 int64          `json:"id"`
	Name               string         `json:"name"`
	Description        string         `json:"description"`
	Status             enums.Status   `json:"status"`
	DraftDefinition    dsl.Definition `json:"draftDefinition"`
	PublishedVersionID int64          `json:"publishedVersionId"`
	SortNo             int            `json:"sortNo"`
	CreatedAt          string         `json:"createdAt"`
	UpdatedAt          string         `json:"updatedAt"`
	CreateUserName     string         `json:"createUserName"`
	UpdateUserName     string         `json:"updateUserName"`
}

type AIWorkflowVersionResponse struct {
	ID              int64          `json:"id"`
	WorkflowID      int64          `json:"workflowId"`
	Version         int            `json:"version"`
	Status          enums.Status   `json:"status"`
	Definition      dsl.Definition `json:"definition"`
	DefinitionHash  string         `json:"definitionHash"`
	PublishedAt     string         `json:"publishedAt"`
	PublishedByID   int64          `json:"publishedById"`
	PublishedByName string         `json:"publishedByName"`
	CreatedAt       string         `json:"createdAt"`
	UpdatedAt       string         `json:"updatedAt"`
}

type AIWorkflowValidationResponse struct {
	Valid  bool                      `json:"valid"`
	Errors []workflowvalidator.Error `json:"errors"`
}

type AIWorkflowTemplateResponse struct {
	Code        string         `json:"code"`
	Name        string         `json:"name"`
	Description string         `json:"description"`
	Definition  dsl.Definition `json:"definition"`
}

type AIWorkflowUsageResponse struct {
	AIAgentID         int64  `json:"aiAgentId"`
	AIAgentName       string `json:"aiAgentName"`
	WorkflowVersionID int64  `json:"workflowVersionId"`
	WorkflowVersion   int    `json:"workflowVersion"`
	Enabled           bool   `json:"enabled"`
}

type AIWorkflowNodeSpecResponse struct {
	Type                            string                          `json:"type"`
	Title                           string                          `json:"title"`
	Description                     string                          `json:"description"`
	Icon                            string                          `json:"icon"`
	Category                        string                          `json:"category"`
	Executable                      bool                            `json:"executable"`
	RiskLevel                       workflowregistry.NodeRiskLevel  `json:"riskLevel"`
	Interruptible                   bool                            `json:"interruptible"`
	RequiresConfirmationPredecessor bool                            `json:"requiresConfirmationPredecessor"`
	ConfigSchema                    any                             `json:"configSchema,omitempty"`
	InputSchema                     []workflowregistry.VariableSpec `json:"inputSchema,omitempty"`
	OutputSchema                    []workflowregistry.VariableSpec `json:"outputSchema,omitempty"`
	DefaultInputs                   map[string]dsl.Value            `json:"defaultInputs,omitempty"`
}

type AIWorkflowRunResponse struct {
	ID                int64                       `json:"id"`
	WorkflowID        int64                       `json:"workflowId"`
	WorkflowVersionID int64                       `json:"workflowVersionId"`
	WorkflowVersion   int                         `json:"workflowVersion"`
	WorkflowName      string                      `json:"workflowName"`
	ConversationID    int64                       `json:"conversationId"`
	AIAgentID         int64                       `json:"aiAgentId"`
	AIAgentName       string                      `json:"aiAgentName"`
	MessageID         int64                       `json:"messageId"`
	Status            int                         `json:"status"`
	StatusName        string                      `json:"statusName"`
	StartedAt         string                      `json:"startedAt"`
	EndedAt           string                      `json:"endedAt"`
	DurationMS        int64                       `json:"durationMs"`
	InterruptType     string                      `json:"interruptType"`
	InterruptNodeID   string                      `json:"interruptNodeId"`
	ErrorMessage      string                      `json:"errorMessage"`
	CreatedAt         string                      `json:"createdAt"`
	UpdatedAt         string                      `json:"updatedAt"`
	Definition        dsl.Definition              `json:"definition"`
	Nodes             []AIWorkflowNodeRunResponse `json:"nodes,omitempty"`
}

type AIWorkflowNodeRunResponse struct {
	ID            int64  `json:"id"`
	WorkflowRunID int64  `json:"workflowRunId"`
	NodeID        string `json:"nodeId"`
	NodeType      string `json:"nodeType"`
	Status        int    `json:"status"`
	StatusName    string `json:"statusName"`
	InputPreview  string `json:"inputPreview"`
	OutputPreview string `json:"outputPreview"`
	ErrorMessage  string `json:"errorMessage"`
	StartedAt     string `json:"startedAt"`
	EndedAt       string `json:"endedAt"`
	DurationMS    int    `json:"durationMs"`
}
