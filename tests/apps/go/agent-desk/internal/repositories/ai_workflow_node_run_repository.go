package repositories

import (
	"agent-desk/internal/models"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

var AIWorkflowNodeRunRepository = newAIWorkflowNodeRunRepository()

func newAIWorkflowNodeRunRepository() *aiWorkflowNodeRunRepository {
	return &aiWorkflowNodeRunRepository{}
}

type aiWorkflowNodeRunRepository struct{}

func (r *aiWorkflowNodeRunRepository) Get(db *gorm.DB, id int64) *models.AIWorkflowNodeRun {
	ret := &models.AIWorkflowNodeRun{}
	if err := db.First(ret, "id = ?", id).Error; err != nil {
		return nil
	}
	return ret
}

func (r *aiWorkflowNodeRunRepository) Find(db *gorm.DB, cnd *sqls.Cnd) (list []models.AIWorkflowNodeRun) {
	cnd.Find(db, &list)
	return
}

func (r *aiWorkflowNodeRunRepository) Create(db *gorm.DB, t *models.AIWorkflowNodeRun) error {
	return db.Create(t).Error
}

func (r *aiWorkflowNodeRunRepository) Updates(db *gorm.DB, id int64, columns map[string]interface{}) error {
	return db.Model(&models.AIWorkflowNodeRun{}).Where("id = ?", id).Updates(columns).Error
}
