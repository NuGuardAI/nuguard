package repositories

import (
	"agent-desk/internal/models"

	"gorm.io/gorm"
)

var AgentToolInvocationRepository = newAgentToolInvocationRepository()

func newAgentToolInvocationRepository() *agentToolInvocationRepository {
	return &agentToolInvocationRepository{}
}

type agentToolInvocationRepository struct{}

func (r *agentToolInvocationRepository) GetByIdempotencyKey(db *gorm.DB, conversationID int64, toolCode, idempotencyKey string) *models.AgentToolInvocation {
	if conversationID <= 0 || toolCode == "" || idempotencyKey == "" {
		return nil
	}
	var item models.AgentToolInvocation
	if err := db.Where("conversation_id = ? AND tool_code = ? AND idempotency_key = ?", conversationID, toolCode, idempotencyKey).First(&item).Error; err != nil {
		return nil
	}
	return &item
}

func (r *agentToolInvocationRepository) Create(db *gorm.DB, item *models.AgentToolInvocation) error {
	return db.Create(item).Error
}

func (r *agentToolInvocationRepository) Updates(db *gorm.DB, id int64, values map[string]any) error {
	return db.Model(&models.AgentToolInvocation{}).Where("id = ?", id).Updates(values).Error
}
