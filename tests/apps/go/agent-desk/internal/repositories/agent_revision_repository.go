package repositories

import (
	"agent-desk/internal/models"

	"gorm.io/gorm"
)

var AgentRevisionRepository = newAgentRevisionRepository()

func newAgentRevisionRepository() *agentRevisionRepository {
	return &agentRevisionRepository{}
}

type agentRevisionRepository struct{}

func (r *agentRevisionRepository) Get(db *gorm.DB, id int64) *models.AgentRevision {
	ret := &models.AgentRevision{}
	if err := db.First(ret, "id = ?", id).Error; err != nil {
		return nil
	}
	return ret
}

func (r *agentRevisionRepository) Create(db *gorm.DB, item *models.AgentRevision) error {
	return db.Create(item).Error
}

func (r *agentRevisionRepository) FindByAgentID(db *gorm.DB, agentID int64) []models.AgentRevision {
	if agentID <= 0 {
		return []models.AgentRevision{}
	}
	items := make([]models.AgentRevision, 0)
	db.Where("agent_id = ?", agentID).Order("revision DESC, id DESC").Find(&items)
	return items
}

func (r *agentRevisionRepository) MaxRevisionByAgentID(db *gorm.DB, agentID int64) int {
	if agentID <= 0 {
		return 0
	}
	var ret int
	db.Model(&models.AgentRevision{}).Where("agent_id = ?", agentID).Select("COALESCE(MAX(revision), 0)").Scan(&ret)
	return ret
}
