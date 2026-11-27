package repositories

import (
	"agent-desk/internal/models"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

var AgentStepRepository = newAgentStepRepository()

func newAgentStepRepository() *agentStepRepository {
	return &agentStepRepository{}
}

type agentStepRepository struct{}

func (r *agentStepRepository) Create(db *gorm.DB, item *models.AgentStep) error {
	return db.Create(item).Error
}

func (r *agentStepRepository) FindByAgentRunID(db *gorm.DB, agentRunID int64) []models.AgentStep {
	if agentRunID <= 0 {
		return []models.AgentStep{}
	}
	return r.Find(db, sqls.NewCnd().Eq("agent_run_id", agentRunID).Asc("id"))
}

func (r *agentStepRepository) LastByAgentRunID(db *gorm.DB, agentRunID int64) *models.AgentStep {
	if agentRunID <= 0 {
		return nil
	}
	ret := &models.AgentStep{}
	if err := db.Where("agent_run_id = ?", agentRunID).Order("id DESC").First(ret).Error; err != nil {
		return nil
	}
	return ret
}

func (r *agentStepRepository) FindByAgentRunIDs(db *gorm.DB, agentRunIDs []int64) []models.AgentStep {
	if len(agentRunIDs) == 0 {
		return nil
	}
	var items []models.AgentStep
	if err := db.Where("agent_run_id IN ?", agentRunIDs).Find(&items).Error; err != nil {
		return nil
	}
	return items
}

func (r *agentStepRepository) Find(db *gorm.DB, cnd *sqls.Cnd) (list []models.AgentStep) {
	cnd.Find(db, &list)
	return
}
