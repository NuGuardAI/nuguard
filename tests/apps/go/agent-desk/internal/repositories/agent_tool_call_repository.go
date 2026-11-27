package repositories

import (
	"agent-desk/internal/models"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

var AgentToolCallRepository = newAgentToolCallRepository()

func newAgentToolCallRepository() *agentToolCallRepository {
	return &agentToolCallRepository{}
}

type agentToolCallRepository struct{}

func (r *agentToolCallRepository) Create(db *gorm.DB, item *models.AgentToolCall) error {
	return db.Create(item).Error
}

func (r *agentToolCallRepository) FindByAgentRunID(db *gorm.DB, agentRunID int64) []models.AgentToolCall {
	if agentRunID <= 0 {
		return []models.AgentToolCall{}
	}
	return r.Find(db, sqls.NewCnd().Eq("agent_run_id", agentRunID).Asc("id"))
}

func (r *agentToolCallRepository) FindByAgentRunIDs(db *gorm.DB, agentRunIDs []int64) []models.AgentToolCall {
	if len(agentRunIDs) == 0 {
		return nil
	}
	var items []models.AgentToolCall
	if err := db.Where("agent_run_id IN ?", agentRunIDs).Find(&items).Error; err != nil {
		return nil
	}
	return items
}

func (r *agentToolCallRepository) Find(db *gorm.DB, cnd *sqls.Cnd) (list []models.AgentToolCall) {
	cnd.Find(db, &list)
	return
}
