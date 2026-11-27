package builders

import (
	"time"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto/response"
)

func BuildAgentRun(item *models.AgentRun) response.AgentRunResponse {
	if item == nil {
		return response.AgentRunResponse{}
	}
	return response.AgentRunResponse{
		ID:               item.ID,
		ConversationID:   item.ConversationID,
		AIAgentID:        item.AIAgentID,
		AgentRevisionID:  item.AgentRevisionID,
		SourceMessageID:  item.SourceMessageID,
		WorkflowRunID:    item.WorkflowRunID,
		Status:           item.Status,
		PromptTokens:     item.PromptTokens,
		CompletionTokens: item.CompletionTokens,
		StartedAt:        formatAgentRunTime(item.StartedAt),
		EndedAt:          formatAgentRunTimePtr(item.EndedAt),
		DurationMS:       agentRunDurationMS(item.StartedAt, item.EndedAt),
		ErrorMessage:     item.ErrorMessage,
		TraceData:        item.TraceData,
		CreatedAt:        formatAgentRunTime(item.CreatedAt),
		UpdatedAt:        formatAgentRunTime(item.UpdatedAt),
	}
}

func BuildAgentRunDetail(item *models.AgentRun, steps []models.AgentStep, toolCalls []models.AgentToolCall, feedback *models.AgentRunQualityFeedback) response.AgentRunResponse {
	ret := BuildAgentRun(item)
	ret.Steps = BuildAgentStepList(steps)
	ret.ToolCalls = BuildAgentToolCallList(toolCalls)
	ret.QualityFeedback = BuildAgentRunQualityFeedback(feedback)
	return ret
}

func BuildAgentRunQualityFeedback(item *models.AgentRunQualityFeedback) *response.AgentRunQualityFeedbackResponse {
	if item == nil {
		return nil
	}
	return &response.AgentRunQualityFeedbackResponse{
		ID:               item.ID,
		AgentRunID:       item.AgentRunID,
		ResolutionStatus: item.ResolutionStatus,
		EvidenceStatus:   item.EvidenceStatus,
		Comment:          item.Comment,
		UpdateUserName:   item.UpdateUserName,
		UpdatedAt:        formatAgentRunTime(item.UpdatedAt),
	}
}

func BuildAgentRunList(list []models.AgentRun) []response.AgentRunResponse {
	ret := make([]response.AgentRunResponse, 0, len(list))
	for i := range list {
		ret = append(ret, BuildAgentRun(&list[i]))
	}
	return ret
}

func BuildAgentStep(item *models.AgentStep) response.AgentStepResponse {
	if item == nil {
		return response.AgentStepResponse{}
	}
	return response.AgentStepResponse{
		ID:            item.ID,
		AgentRunID:    item.AgentRunID,
		WorkflowRunID: item.WorkflowRunID,
		StepType:      item.StepType,
		StepCode:      item.StepCode,
		Status:        item.Status,
		InputPreview:  item.InputPreview,
		OutputPreview: item.OutputPreview,
		ErrorMessage:  item.ErrorMessage,
		StartedAt:     formatAgentRunTime(item.StartedAt),
		EndedAt:       formatAgentRunTimePtr(item.EndedAt),
		DurationMS:    item.DurationMS,
	}
}

func BuildAgentStepList(list []models.AgentStep) []response.AgentStepResponse {
	ret := make([]response.AgentStepResponse, 0, len(list))
	for i := range list {
		ret = append(ret, BuildAgentStep(&list[i]))
	}
	return ret
}

func BuildAgentToolCall(item *models.AgentToolCall) response.AgentToolCallResponse {
	if item == nil {
		return response.AgentToolCallResponse{}
	}
	return response.AgentToolCallResponse{
		ID:               item.ID,
		AgentRunID:       item.AgentRunID,
		AgentStepID:      item.AgentStepID,
		ToolCode:         item.ToolCode,
		RiskLevel:        item.RiskLevel,
		RequireConfirm:   item.RequireConfirm,
		Status:           item.Status,
		ArgumentsPreview: item.ArgumentsPreview,
		ResultPreview:    item.ResultPreview,
		ErrorMessage:     item.ErrorMessage,
		DurationMS:       item.DurationMS,
		CreatedAt:        formatAgentRunTime(item.CreatedAt),
	}
}

func BuildAgentToolCallList(list []models.AgentToolCall) []response.AgentToolCallResponse {
	ret := make([]response.AgentToolCallResponse, 0, len(list))
	for i := range list {
		ret = append(ret, BuildAgentToolCall(&list[i]))
	}
	return ret
}

func formatAgentRunTime(value time.Time) string {
	if value.IsZero() {
		return ""
	}
	return value.Format("2006-01-02 15:04:05")
}

func formatAgentRunTimePtr(value *time.Time) string {
	if value == nil {
		return ""
	}
	return formatAgentRunTime(*value)
}

func agentRunDurationMS(startedAt time.Time, endedAt *time.Time) int64 {
	if startedAt.IsZero() || endedAt == nil || endedAt.IsZero() {
		return 0
	}
	duration := endedAt.Sub(startedAt).Milliseconds()
	if duration < 0 {
		return 0
	}
	return duration
}
