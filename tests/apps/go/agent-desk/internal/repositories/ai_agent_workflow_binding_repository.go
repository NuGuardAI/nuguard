package repositories

import (
	"agent-desk/internal/models"

	"gorm.io/gorm"
)

var AIAgentWorkflowBindingRepository = newAIAgentWorkflowBindingRepository()

func newAIAgentWorkflowBindingRepository() *aiAgentWorkflowBindingRepository {
	return &aiAgentWorkflowBindingRepository{}
}

type aiAgentWorkflowBindingRepository struct{}

func (r *aiAgentWorkflowBindingRepository) FindByAgentID(db *gorm.DB, agentID int64) []models.AIAgentWorkflowBinding {
	ret := make([]models.AIAgentWorkflowBinding, 0)
	if agentID > 0 {
		db.Where("ai_agent_id = ?", agentID).Order("priority ASC, id ASC").Find(&ret)
	}
	return ret
}

func (r *aiAgentWorkflowBindingRepository) FindEnabledByAgentID(db *gorm.DB, agentID int64) []models.AIAgentWorkflowBinding {
	ret := make([]models.AIAgentWorkflowBinding, 0)
	if agentID > 0 {
		db.Where("ai_agent_id = ? AND enabled = ?", agentID, true).Order("priority ASC, id ASC").Find(&ret)
	}
	return ret
}

func (r *aiAgentWorkflowBindingRepository) FindByWorkflowID(db *gorm.DB, workflowID int64) []models.AIAgentWorkflowBinding {
	ret := make([]models.AIAgentWorkflowBinding, 0)
	if workflowID > 0 {
		db.Where("workflow_id = ?", workflowID).Order("priority ASC, id ASC").Find(&ret)
	}
	return ret
}

func (r *aiAgentWorkflowBindingRepository) CountByWorkflowID(db *gorm.DB, workflowID int64) int64 {
	var count int64
	if workflowID > 0 {
		db.Model(&models.AIAgentWorkflowBinding{}).Where("workflow_id = ?", workflowID).Count(&count)
	}
	return count
}

func (r *aiAgentWorkflowBindingRepository) ReplaceByAgentID(db *gorm.DB, agentID int64, items []models.AIAgentWorkflowBinding) error {
	if err := db.Where("ai_agent_id = ?", agentID).Delete(&models.AIAgentWorkflowBinding{}).Error; err != nil {
		return err
	}
	if len(items) == 0 {
		return nil
	}
	return db.Create(&items).Error
}
