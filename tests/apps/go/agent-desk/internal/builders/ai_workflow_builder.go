package builders

import (
	"encoding/json"
	"time"

	"agent-desk/internal/ai/workflow/dsl"
	workflowregistry "agent-desk/internal/ai/workflow/registry"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/services"
)

func BuildAIWorkflow(item *models.AIWorkflow) response.AIWorkflowResponse {
	if item == nil {
		return response.AIWorkflowResponse{}
	}
	return response.AIWorkflowResponse{
		ID:                 item.ID,
		Name:               item.Name,
		Description:        item.Description,
		Status:             item.Status,
		DraftDefinition:    parseWorkflowDefinition(item.DraftDefinition),
		PublishedVersionID: item.PublishedVersionID,
		SortNo:             item.SortNo,
		CreatedAt:          item.CreatedAt.Format("2006-01-02 15:04:05"),
		UpdatedAt:          item.UpdatedAt.Format("2006-01-02 15:04:05"),
		CreateUserName:     item.CreateUserName,
		UpdateUserName:     item.UpdateUserName,
	}
}

func BuildAIWorkflowList(list []models.AIWorkflow) []response.AIWorkflowResponse {
	ret := make([]response.AIWorkflowResponse, 0, len(list))
	for i := range list {
		ret = append(ret, BuildAIWorkflow(&list[i]))
	}
	return ret
}

func BuildAIWorkflowVersion(item *models.AIWorkflowVersion) response.AIWorkflowVersionResponse {
	if item == nil {
		return response.AIWorkflowVersionResponse{}
	}
	publishedAt := ""
	if item.PublishedAt != nil {
		publishedAt = item.PublishedAt.Format("2006-01-02 15:04:05")
	}
	return response.AIWorkflowVersionResponse{
		ID:              item.ID,
		WorkflowID:      item.WorkflowID,
		Version:         item.Version,
		Status:          item.Status,
		Definition:      parseWorkflowDefinition(item.Definition),
		DefinitionHash:  item.DefinitionHash,
		PublishedAt:     publishedAt,
		PublishedByID:   item.PublishedByID,
		PublishedByName: item.PublishedByName,
		CreatedAt:       item.CreatedAt.Format("2006-01-02 15:04:05"),
		UpdatedAt:       item.UpdatedAt.Format("2006-01-02 15:04:05"),
	}
}

func BuildAIWorkflowVersionList(list []models.AIWorkflowVersion) []response.AIWorkflowVersionResponse {
	ret := make([]response.AIWorkflowVersionResponse, 0, len(list))
	for i := range list {
		ret = append(ret, BuildAIWorkflowVersion(&list[i]))
	}
	return ret
}

func BuildAIWorkflowNodeSpecs(list []workflowregistry.NodeSpec) []response.AIWorkflowNodeSpecResponse {
	ret := make([]response.AIWorkflowNodeSpecResponse, 0, len(list))
	for _, item := range list {
		ret = append(ret, response.AIWorkflowNodeSpecResponse{
			Type:                            item.Type,
			Title:                           item.Title,
			Description:                     item.Description,
			Icon:                            item.Icon,
			Category:                        item.Category,
			Executable:                      item.Executable,
			RiskLevel:                       item.RiskLevel,
			Interruptible:                   item.Interruptible,
			RequiresConfirmationPredecessor: item.RequiresConfirmationPredecessor,
			ConfigSchema:                    item.ConfigSchema,
			InputSchema:                     item.InputSchema,
			OutputSchema:                    item.OutputSchema,
			DefaultInputs:                   item.DefaultInputs,
		})
	}
	return ret
}

func BuildAIWorkflowTemplates(list []services.AIWorkflowTemplate) []response.AIWorkflowTemplateResponse {
	ret := make([]response.AIWorkflowTemplateResponse, 0, len(list))
	for _, item := range list {
		ret = append(ret, response.AIWorkflowTemplateResponse{Code: item.Code, Name: item.Name, Description: item.Description, Definition: item.Definition})
	}
	return ret
}

func BuildAIWorkflowRun(item *models.AIWorkflowRun) response.AIWorkflowRunResponse {
	return BuildAIWorkflowRunWithContext(item, nil, nil, nil)
}

func BuildAIWorkflowRunWithContext(item *models.AIWorkflowRun, workflow *models.AIWorkflow, version *models.AIWorkflowVersion, agent *models.AIAgent) response.AIWorkflowRunResponse {
	if item == nil {
		return response.AIWorkflowRunResponse{}
	}
	ret := response.AIWorkflowRunResponse{
		ID:                item.ID,
		WorkflowID:        item.WorkflowID,
		WorkflowVersionID: item.WorkflowVersionID,
		ConversationID:    item.ConversationID,
		AIAgentID:         item.AIAgentID,
		MessageID:         item.MessageID,
		Status:            item.Status,
		StatusName:        workflowRunStatusName(item.Status),
		StartedAt:         formatWorkflowTime(item.StartedAt),
		EndedAt:           formatWorkflowTimePtr(item.EndedAt),
		DurationMS:        workflowRunDurationMS(item.StartedAt, item.EndedAt),
		InterruptType:     item.InterruptType,
		InterruptNodeID:   item.InterruptNodeID,
		ErrorMessage:      item.ErrorMessage,
		CreatedAt:         formatWorkflowTime(item.CreatedAt),
		UpdatedAt:         formatWorkflowTime(item.UpdatedAt),
	}
	if workflow != nil {
		ret.WorkflowName = workflow.Name
	}
	if version != nil {
		ret.WorkflowVersion = version.Version
	}
	if agent != nil {
		ret.AIAgentName = agent.Name
	}
	return ret
}

func BuildAIWorkflowRunDetail(item *models.AIWorkflowRun, nodes []models.AIWorkflowNodeRun) response.AIWorkflowRunResponse {
	ret := BuildAIWorkflowRun(item)
	ret.Nodes = BuildAIWorkflowNodeRunList(nodes)
	return ret
}

func BuildAIWorkflowRunDetailWithContext(item *models.AIWorkflowRun, nodes []models.AIWorkflowNodeRun, workflow *models.AIWorkflow, version *models.AIWorkflowVersion, agent *models.AIAgent) response.AIWorkflowRunResponse {
	ret := BuildAIWorkflowRunWithContext(item, workflow, version, agent)
	if version != nil {
		ret.Definition = parseWorkflowDefinition(version.Definition)
	}
	ret.Nodes = BuildAIWorkflowNodeRunList(nodes)
	return ret
}

func BuildAIWorkflowRunList(list []models.AIWorkflowRun) []response.AIWorkflowRunResponse {
	ret := make([]response.AIWorkflowRunResponse, 0, len(list))
	for i := range list {
		ret = append(ret, BuildAIWorkflowRun(&list[i]))
	}
	return ret
}

func BuildAIWorkflowNodeRun(item *models.AIWorkflowNodeRun) response.AIWorkflowNodeRunResponse {
	if item == nil {
		return response.AIWorkflowNodeRunResponse{}
	}
	return response.AIWorkflowNodeRunResponse{
		ID:            item.ID,
		WorkflowRunID: item.WorkflowRunID,
		NodeID:        item.NodeID,
		NodeType:      item.NodeType,
		Status:        item.Status,
		StatusName:    workflowRunStatusName(item.Status),
		InputPreview:  item.InputPreview,
		OutputPreview: item.OutputPreview,
		ErrorMessage:  item.ErrorMessage,
		StartedAt:     formatWorkflowTime(item.StartedAt),
		EndedAt:       formatWorkflowTimePtr(item.EndedAt),
		DurationMS:    item.DurationMS,
	}
}

func BuildAIWorkflowNodeRunList(list []models.AIWorkflowNodeRun) []response.AIWorkflowNodeRunResponse {
	ret := make([]response.AIWorkflowNodeRunResponse, 0, len(list))
	for i := range list {
		ret = append(ret, BuildAIWorkflowNodeRun(&list[i]))
	}
	return ret
}

func parseWorkflowDefinition(raw string) dsl.Definition {
	var ret dsl.Definition
	if raw == "" {
		return ret
	}
	_ = json.Unmarshal([]byte(raw), &ret)
	return ret
}

func workflowRunStatusName(status int) string {
	switch status {
	case 1:
		return "completed"
	case 2:
		return "interrupted"
	case 3:
		return "failed"
	default:
		return "unknown"
	}
}

func formatWorkflowTime(value time.Time) string {
	if value.IsZero() {
		return ""
	}
	return value.Format("2006-01-02 15:04:05")
}

func formatWorkflowTimePtr(value *time.Time) string {
	if value == nil {
		return ""
	}
	return formatWorkflowTime(*value)
}

func workflowRunDurationMS(startedAt time.Time, endedAt *time.Time) int64 {
	if startedAt.IsZero() || endedAt == nil || endedAt.IsZero() {
		return 0
	}
	return endedAt.Sub(startedAt).Milliseconds()
}
