package response

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
)

type AIAgentTeamResponse struct {
	ID   int64  `json:"id"`
	Name string `json:"name"`
}

type AIAgentSkillResponse struct {
	ID   int64  `json:"id"`
	Name string `json:"name"`
}

type AIAgentMCPToolResponse struct {
	ToolCode            string            `json:"toolCode"`
	ServerCode          string            `json:"serverCode"`
	ToolName            string            `json:"toolName"`
	Title               string            `json:"title"`
	Description         string            `json:"description"`
	RiskLevel           string            `json:"riskLevel"`
	RequireConfirmation bool              `json:"requireConfirmation"`
	Arguments           map[string]string `json:"arguments"`
}

type AIAgentWorkflowBindingResponse struct {
	ID                 int64  `json:"id"`
	WorkflowID         int64  `json:"workflowId"`
	WorkflowVersionID  int64  `json:"workflowVersionId"`
	WorkflowName       string `json:"workflowName"`
	WorkflowVersion    int    `json:"workflowVersion"`
	ToolName           string `json:"toolName"`
	TriggerInstruction string `json:"triggerInstruction"`
	Priority           int    `json:"priority"`
	Enabled            bool   `json:"enabled"`
}

type AgentRevisionResponse struct {
	ID              int64        `json:"id"`
	AgentID         int64        `json:"agentId"`
	Revision        int          `json:"revision"`
	Status          enums.Status `json:"status"`
	DefinitionHash  string       `json:"definitionHash"`
	PublishedAt     string       `json:"publishedAt"`
	PublishedByID   int64        `json:"publishedById"`
	PublishedByName string       `json:"publishedByName"`
}

type AIConfigResponse struct {
	ID               int64             `json:"id"`
	Name             string            `json:"name"`
	Provider         enums.AIProvider  `json:"provider"`
	BaseURL          string            `json:"baseUrl"`
	HasAPIKey        bool              `json:"hasApiKey"`
	ModelType        enums.AIModelType `json:"modelType"`
	ModelName        string            `json:"modelName"`
	Dimension        int               `json:"dimension"`
	MaxContextTokens int               `json:"maxContextTokens"`
	MaxOutputTokens  int               `json:"maxOutputTokens"`
	TimeoutMS        int               `json:"timeoutMs"`
	MaxRetryCount    int               `json:"maxRetryCount"`
	RPMLimit         int               `json:"rpmLimit"`
	TPMLimit         int               `json:"tpmLimit"`
	Status           enums.Status      `json:"status"`
	SortNo           int               `json:"sortNo"`
	Remark           string            `json:"remark"`
}

func BuildAIConfigResponse(item *models.AIConfig) AIConfigResponse {
	return AIConfigResponse{
		ID:               item.ID,
		Name:             item.Name,
		Provider:         item.Provider,
		BaseURL:          item.BaseURL,
		HasAPIKey:        item.APIKey != "",
		ModelType:        item.ModelType,
		ModelName:        item.ModelName,
		Dimension:        item.Dimension,
		MaxContextTokens: item.MaxContextTokens,
		MaxOutputTokens:  item.MaxOutputTokens,
		TimeoutMS:        item.TimeoutMS,
		MaxRetryCount:    item.MaxRetryCount,
		RPMLimit:         item.RPMLimit,
		TPMLimit:         item.TPMLimit,
		Status:           item.Status,
		SortNo:           item.SortNo,
		Remark:           item.Remark,
	}
}

type AIAgentResponse struct {
	ID                     int64                            `json:"id"`
	Name                   string                           `json:"name"`
	Description            string                           `json:"description"`
	Status                 enums.Status                     `json:"status"`
	StatusName             string                           `json:"statusName"`
	AIConfigID             int64                            `json:"aiConfigId"`
	AIConfigName           string                           `json:"aiConfigName"`
	MaxSteps               int                              `json:"maxSteps"`
	ContextWindow          int                              `json:"contextWindow"`
	ToolPolicy             string                           `json:"toolPolicy"`
	KnowledgePolicy        string                           `json:"knowledgePolicy"`
	ServiceMode            enums.IMConversationServiceMode  `json:"serviceMode"`
	ServiceModeName        string                           `json:"serviceModeName"`
	SystemPrompt           string                           `json:"systemPrompt"`
	WelcomeMessage         string                           `json:"welcomeMessage"`
	ReplyTimeoutSeconds    int                              `json:"replyTimeoutSeconds"`
	RolloutPercent         int                              `json:"rolloutPercent"`
	PreviousRolloutPercent int                              `json:"previousRolloutPercent"`
	Teams                  []AIAgentTeamResponse            `json:"teams"`
	HandoffMode            enums.AIAgentHandoffMode         `json:"handoffMode"`
	HandoffModeName        string                           `json:"handoffModeName"`
	FallbackMode           enums.AIAgentFallbackMode        `json:"fallbackMode"`
	FallbackModeName       string                           `json:"fallbackModeName"`
	FallbackMessage        string                           `json:"fallbackMessage"`
	KnowledgeBaseIDs       []int64                          `json:"knowledgeBaseIds"`
	SkillIDs               []int64                          `json:"skillIds"`
	Skills                 []AIAgentSkillResponse           `json:"skills"`
	MCPTools               []AIAgentMCPToolResponse         `json:"mcpTools"`
	WorkflowBindings       []AIAgentWorkflowBindingResponse `json:"workflowBindings"`
	PublishedRevisionID    int64                            `json:"publishedRevisionId"`
	SortNo                 int                              `json:"sortNo"`
	CreatedAt              string                           `json:"createdAt"`
	UpdatedAt              string                           `json:"updatedAt"`
	CreateUserName         string                           `json:"createUserName"`
	UpdateUserName         string                           `json:"updateUserName"`
}
