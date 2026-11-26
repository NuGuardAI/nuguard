package repositories

import (
	"agent-desk/internal/models"

	"gorm.io/gorm"
)

var AgentRunQualityFeedbackRepository = newAgentRunQualityFeedbackRepository()

func newAgentRunQualityFeedbackRepository() *agentRunQualityFeedbackRepository {
	return &agentRunQualityFeedbackRepository{}
}

type agentRunQualityFeedbackRepository struct{}

func (r *agentRunQualityFeedbackRepository) GetByAgentRunID(db *gorm.DB, agentRunID int64) *models.AgentRunQualityFeedback {
	if agentRunID <= 0 {
		return nil
	}
	item := &models.AgentRunQualityFeedback{}
	if err := db.Where("agent_run_id = ?", agentRunID).First(item).Error; err != nil {
		return nil
	}
	return item
}

func (r *agentRunQualityFeedbackRepository) FindByAgentRunIDs(db *gorm.DB, agentRunIDs []int64) []models.AgentRunQualityFeedback {
	if len(agentRunIDs) == 0 {
		return []models.AgentRunQualityFeedback{}
	}
	var items []models.AgentRunQualityFeedback
	if err := db.Where("agent_run_id IN ?", agentRunIDs).Find(&items).Error; err != nil {
		return []models.AgentRunQualityFeedback{}
	}
	return items
}

func (r *agentRunQualityFeedbackRepository) Create(db *gorm.DB, item *models.AgentRunQualityFeedback) error {
	return db.Create(item).Error
}

func (r *agentRunQualityFeedbackRepository) Updates(db *gorm.DB, id int64, columns map[string]any) error {
	return db.Model(&models.AgentRunQualityFeedback{}).Where("id = ?", id).Updates(columns).Error
}
