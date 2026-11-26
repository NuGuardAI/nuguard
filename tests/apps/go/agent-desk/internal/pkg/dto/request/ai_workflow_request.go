package request

import "agent-desk/internal/ai/workflow/dsl"

type CreateAIWorkflowRequest struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	Definition  dsl.Definition `json:"definition"`
}

type UpdateAIWorkflowRequest struct {
	ID int64 `json:"id"`
	CreateAIWorkflowRequest
}

type DeleteAIWorkflowRequest struct {
	ID int64 `json:"id"`
}

type ValidateAIWorkflowRequest struct {
	Definition dsl.Definition `json:"definition"`
}

type PublishAIWorkflowRequest struct {
	WorkflowID int64          `json:"workflowId"`
	Definition dsl.Definition `json:"definition"`
}

type AIWorkflowVersionListRequest struct {
	WorkflowID int64 `json:"workflowId"`
}

type RestoreAIWorkflowVersionRequest struct {
	WorkflowID        int64 `json:"workflowId"`
	WorkflowVersionID int64 `json:"workflowVersionId"`
}
