package services

import (
	"strings"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/utils"
	"agent-desk/internal/repositories"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

type AIAgentWorkflowBindingContext struct {
	Binding  models.AIAgentWorkflowBinding
	Workflow *models.AIWorkflow
	Version  *models.AIWorkflowVersion
}

func (s *aIAgentService) ListWorkflowBindings(agentID int64) []AIAgentWorkflowBindingContext {
	bindings := repositories.AIAgentWorkflowBindingRepository.FindByAgentID(sqls.DB(), agentID)
	return s.buildWorkflowBindingContexts(sqls.DB(), bindings)
}

func (s *aIAgentService) ListEnabledWorkflowBindings(db *gorm.DB, agentID int64) []AIAgentWorkflowBindingContext {
	return s.buildWorkflowBindingContexts(db, repositories.AIAgentWorkflowBindingRepository.FindEnabledByAgentID(db, agentID))
}

func (s *aIAgentService) buildWorkflowBindingContexts(db *gorm.DB, bindings []models.AIAgentWorkflowBinding) []AIAgentWorkflowBindingContext {
	ret := make([]AIAgentWorkflowBindingContext, 0, len(bindings))
	for _, binding := range bindings {
		ret = append(ret, AIAgentWorkflowBindingContext{Binding: binding, Workflow: repositories.AIWorkflowRepository.Get(db, binding.WorkflowID), Version: repositories.AIWorkflowVersionRepository.Get(db, binding.WorkflowVersionID)})
	}
	return ret
}

func (s *aIAgentService) replaceWorkflowBindings(db *gorm.DB, agentID int64, input []request.AIAgentWorkflowBindingRequest, operator *dto.AuthPrincipal) ([]models.AIAgentWorkflowBinding, error) {
	seen := make(map[int64]struct{}, len(input))
	items := make([]models.AIAgentWorkflowBinding, 0, len(input))
	for index, item := range input {
		if item.WorkflowVersionID <= 0 {
			return nil, errorsx.InvalidParam("workflow version is required")
		}
		if _, exists := seen[item.WorkflowVersionID]; exists {
			return nil, errorsx.InvalidParam("workflow version must not be bound more than once")
		}
		seen[item.WorkflowVersionID] = struct{}{}
		version := repositories.AIWorkflowVersionRepository.Get(db, item.WorkflowVersionID)
		if version == nil || version.Status != enums.StatusOk {
			return nil, errorsx.InvalidParam("workflow version is not published")
		}
		workflow := repositories.AIWorkflowRepository.Get(db, version.WorkflowID)
		if workflow == nil || workflow.Status == enums.StatusDeleted {
			return nil, errorsx.InvalidParam("workflow does not exist")
		}
		priority := item.Priority
		if priority == 0 {
			priority = index + 1
		}
		items = append(items, models.AIAgentWorkflowBinding{AIAgentID: agentID, WorkflowID: version.WorkflowID, WorkflowVersionID: version.ID, ToolName: strings.TrimSpace(item.ToolName), TriggerInstruction: strings.TrimSpace(item.TriggerInstruction), Priority: priority, Enabled: item.Enabled, AuditFields: utils.BuildAuditFields(operator)})
	}
	if err := repositories.AIAgentWorkflowBindingRepository.ReplaceByAgentID(db, agentID, items); err != nil {
		return nil, err
	}
	return items, nil
}
