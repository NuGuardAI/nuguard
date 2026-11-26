package runtime

import (
	"context"
	"fmt"
	"strings"

	applicationruntime "agent-desk/internal/ai/application/runtime"
	"agent-desk/internal/ai/runtime/graphs"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	svc "agent-desk/internal/services"
)

func init() {
	svc.SkillDebugRunHook = DebugRunSkill
	svc.SkillDebugResumeHook = DebugResumeSkill
}

func DebugRunSkill(ctx context.Context, req request.SkillDebugRunRequest) (*response.SkillDebugRunResponse, error) {
	aiAgent := svc.AIAgentService.Get(req.AIAgentID)
	if aiAgent == nil || aiAgent.Status != enums.StatusOk {
		return nil, errorsx.InvalidParamI18n("error.e0007")
	}
	aiConfig := svc.AIConfigService.Get(aiAgent.AIConfigID)
	if aiConfig == nil {
		return nil, errorsx.InvalidParamI18n("error.e0008")
	}
	skill := svc.SkillDefinitionService.Get(req.SkillDefinitionID)
	if skill == nil || skill.Status != enums.StatusOk {
		return nil, errorsx.InvalidParamI18n("error.e0054")
	}
	debugAgent := *aiAgent
	debugAgent.SkillIDs = fmt.Sprintf("%d", skill.ID)
	var conversation *models.Conversation
	if req.ConversationID > 0 {
		if conversation = svc.ConversationService.Get(req.ConversationID); conversation == nil {
			return nil, errorsx.InvalidParamI18n("error.e0116")
		}
	} else {
		conversation = &models.Conversation{ID: req.ConversationID, AIAgentID: req.AIAgentID}
	}
	message := models.Message{
		ConversationID: req.ConversationID,
		SenderType:     enums.IMSenderTypeCustomer,
		MessageType:    enums.IMMessageTypeText,
		Content:        strings.TrimSpace(req.UserMessage),
	}
	summary, err := applicationruntime.DefaultAgentApplicationService.RunPrepared(ctx, applicationruntime.RunInput{
		Conversation: *conversation,
		UserMessage:  message,
		AIAgent:      debugAgent,
		AIConfig:     *aiConfig,
		Debug:        true,
	})
	if err != nil {
		return buildSkillDebugRunResponse(req, summary, skill), err
	}
	return buildSkillDebugRunResponse(req, summary, skill), nil
}

func DebugResumeSkill(ctx context.Context, req request.SkillDebugResumeRequest) (*response.SkillDebugRunResponse, error) {
	aiAgent := svc.AIAgentService.Get(req.AIAgentID)
	if aiAgent == nil || aiAgent.Status != enums.StatusOk {
		return nil, errorsx.InvalidParamI18n("error.e0007")
	}
	aiConfig := svc.AIConfigService.Get(aiAgent.AIConfigID)
	if aiConfig == nil {
		return nil, errorsx.InvalidParamI18n("error.e0008")
	}
	pendingInterrupt := svc.ConversationInterruptService.GetByCheckPointID(strings.TrimSpace(req.CheckPointID))
	if pendingInterrupt == nil {
		return nil, errorsx.InvalidParamI18n("error.e0014")
	}
	if pendingInterrupt.AIAgentID > 0 && pendingInterrupt.AIAgentID != req.AIAgentID {
		return nil, errorsx.InvalidParamI18n("error.e0015")
	}
	conversationID := req.ConversationID
	if conversationID <= 0 {
		conversationID = pendingInterrupt.ConversationID
	}
	if conversationID <= 0 {
		return nil, errorsx.InvalidParamI18n("error.e0116")
	}
	conversation := svc.ConversationService.Get(conversationID)
	if conversation == nil {
		return nil, errorsx.InvalidParamI18n("error.e0116")
	}
	if conversation.AIAgentID > 0 && conversation.AIAgentID != req.AIAgentID {
		return nil, errorsx.InvalidParamI18n("error.e0117")
	}
	resumeText := strings.TrimSpace(req.UserMessage)
	summary, err := applicationruntime.DefaultAgentApplicationService.ResumePrepared(ctx, applicationruntime.ResumeInput{
		Conversation: *conversation,
		AIAgent:      *aiAgent,
		AIConfig:     *aiConfig,
		CheckPointID: strings.TrimSpace(req.CheckPointID),
		ResumeData: map[string]string{
			strings.TrimSpace(pendingInterrupt.InterruptID): resumeText,
		},
		Debug: true,
	})
	if err != nil {
		if isCheckpointMissingError(err) {
			summary = &applicationruntime.RunResult{
				Status:    "expired",
				ReplyText: graphs.ConfirmationExpiredReply,
			}
			if pendingInterrupt.ID > 0 {
				_ = svc.ConversationInterruptService.MarkExpired(pendingInterrupt.ID, 0)
			}
			return buildSkillDebugResumeResponse(req, summary, conversationID), nil
		}
		return buildSkillDebugResumeResponse(req, summary, conversationID), err
	}
	if pendingInterrupt.ID > 0 {
		if summary != nil && summary.Interrupted {
			_ = svc.ConversationInterruptService.MarkPendingAgain(pendingInterrupt.ID, firstInterruptID(summary), resolveInterruptPrompt(summary), 0)
		} else if summary != nil && graphs.IsCancellationReply(summary.ReplyText) {
			_ = svc.ConversationInterruptService.MarkCancelled(pendingInterrupt.ID, 0)
		} else {
			_ = svc.ConversationInterruptService.MarkResolved(pendingInterrupt.ID, 0)
		}
	}
	return buildSkillDebugResumeResponse(req, summary, conversationID), nil
}

func buildSkillDebugRunResponse(req request.SkillDebugRunRequest, summary *applicationruntime.RunResult, skill *models.SkillDefinition) *response.SkillDebugRunResponse {
	resp := &response.SkillDebugRunResponse{
		ConversationID: req.ConversationID,
		AIAgentID:      req.AIAgentID,
	}
	if skill != nil {
		resp.SkillDefinitionID = skill.ID
		resp.SkillName = skill.Name
	}
	if summary == nil {
		return resp
	}
	if resp.SkillDefinitionID <= 0 {
		resp.SkillDefinitionID = summary.PlannedSkillID
	}
	resp.ReplyText = summary.ReplyText
	resp.ToolWhitelist = append([]string(nil), summary.SkillAllowedToolCodes...)
	resp.InvokedToolCodes = append([]string(nil), summary.InvokedToolCodes...)
	resp.InterruptType = firstInterruptType(summary)
	resp.CheckPointID = summary.CheckPointID
	resp.Interrupted = summary.Interrupted
	resp.TraceData = summary.TraceData
	resp.ErrorMessage = summary.ErrorMessage
	return resp
}

func buildSkillDebugResumeResponse(req request.SkillDebugResumeRequest, summary *applicationruntime.RunResult, conversationID int64) *response.SkillDebugRunResponse {
	resp := &response.SkillDebugRunResponse{
		ConversationID: conversationID,
		AIAgentID:      req.AIAgentID,
	}
	if summary == nil {
		return resp
	}
	resp.SkillDefinitionID = summary.PlannedSkillID
	resp.SkillName = strings.TrimSpace(summary.PlannedSkillName)
	resp.ReplyText = summary.ReplyText
	resp.ToolWhitelist = append([]string(nil), summary.SkillAllowedToolCodes...)
	resp.InvokedToolCodes = append([]string(nil), summary.InvokedToolCodes...)
	resp.InterruptType = firstInterruptType(summary)
	resp.CheckPointID = summary.CheckPointID
	resp.Interrupted = summary.Interrupted
	resp.TraceData = summary.TraceData
	resp.ErrorMessage = summary.ErrorMessage
	return resp
}
