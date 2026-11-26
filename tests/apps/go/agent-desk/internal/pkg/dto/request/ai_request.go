package request

import "agent-desk/internal/pkg/enums"

type AIAgentMCPToolRequest struct {
	ToolCode            string            `json:"toolCode"`
	ServerCode          string            `json:"serverCode"`
	ToolName            string            `json:"toolName"`
	Title               string            `json:"title"`
	Description         string            `json:"description"`
	RiskLevel           string            `json:"riskLevel"`
	RequireConfirmation bool              `json:"requireConfirmation"`
	Arguments           map[string]string `json:"arguments"`
}

type AIAgentWorkflowBindingRequest struct {
	WorkflowVersionID  int64  `json:"workflowVersionId"`
	ToolName           string `json:"toolName"`
	TriggerInstruction string `json:"triggerInstruction"`
	Priority           int    `json:"priority"`
	Enabled            bool   `json:"enabled"`
}

type CreateAIConfigRequest struct {
	Name             string            `json:"name"`
	Provider         enums.AIProvider  `json:"provider"`
	BaseURL          string            `json:"baseUrl"`
	APIKey           string            `json:"apiKey"`
	ModelType        enums.AIModelType `json:"modelType"`
	ModelName        string            `json:"modelName"`
	Dimension        int               `json:"dimension"`
	MaxContextTokens int               `json:"maxContextTokens"`
	MaxOutputTokens  int               `json:"maxOutputTokens"`
	TimeoutMS        int               `json:"timeoutMs"`
	MaxRetryCount    int               `json:"maxRetryCount"`
	RPMLimit         int               `json:"rpmLimit"`
	TPMLimit         int               `json:"tpmLimit"`
	Remark           string            `json:"remark"`
}

type UpdateAIConfigRequest struct {
	ID int64 `json:"id"`
	CreateAIConfigRequest
}

type DeleteAIConfigRequest struct {
	ID int64 `json:"id"`
}

type UpdateAIConfigStatusRequest struct {
	ID     int64        `json:"id"`
	Status enums.Status `json:"status"`
}

type CreateAIAgentRequest struct {
	Name                string                          `json:"name"`
	Description         string                          `json:"description"`
	AIConfigID          int64                           `json:"aiConfigId"`
	MaxSteps            int                             `json:"maxSteps"`
	ContextWindow       int                             `json:"contextWindow"`
	ToolPolicy          string                          `json:"toolPolicy"`
	KnowledgePolicy     string                          `json:"knowledgePolicy"`
	ServiceMode         enums.IMConversationServiceMode `json:"serviceMode"`
	SystemPrompt        string                          `json:"systemPrompt"`
	WelcomeMessage      string                          `json:"welcomeMessage"`
	ReplyTimeoutSeconds int                             `json:"replyTimeoutSeconds"`
	RolloutPercent      int                             `json:"rolloutPercent"`
	TeamIDs             []int64                         `json:"teamIds"`
	HandoffMode         enums.AIAgentHandoffMode        `json:"handoffMode"`
	FallbackMode        enums.AIAgentFallbackMode       `json:"fallbackMode"`
	FallbackMessage     string                          `json:"fallbackMessage"`
	KnowledgeBaseIDs    []int64                         `json:"knowledgeBaseIds"`
	SkillIDs            []int64                         `json:"skillIds"`
	MCPTools            []AIAgentMCPToolRequest         `json:"mcpTools"`
	WorkflowBindings    []AIAgentWorkflowBindingRequest `json:"workflowBindings"`
}

type UpdateAIAgentRequest struct {
	ID int64 `json:"id"`
	CreateAIAgentRequest
}

type DeleteAIAgentRequest struct {
	ID int64 `json:"id"`
}

type PublishAIAgentRequest struct {
	ID int64 `json:"id"`
}

type RollbackAIAgentRequest struct {
	ID         int64 `json:"id"`
	RevisionID int64 `json:"revisionId"`
}

type RollbackAIAgentRolloutRequest struct {
	ID int64 `json:"id"`
}

type UpdateAIAgentStatusRequest struct {
	ID     int64 `json:"id"`
	Status int   `json:"status"`
}
