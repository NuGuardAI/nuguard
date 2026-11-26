package repositories

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

var AIWorkflowRepository = newAIWorkflowRepository()

func newAIWorkflowRepository() *aiWorkflowRepository {
	return &aiWorkflowRepository{}
}

type aiWorkflowRepository struct{}

func (r *aiWorkflowRepository) Get(db *gorm.DB, id int64) *models.AIWorkflow {
	ret := &models.AIWorkflow{}
	if err := db.First(ret, "id = ?", id).Error; err != nil {
		return nil
	}
	return ret
}

func (r *aiWorkflowRepository) Take(db *gorm.DB, where ...interface{}) *models.AIWorkflow {
	ret := &models.AIWorkflow{}
	if err := db.Take(ret, where...).Error; err != nil {
		return nil
	}
	return ret
}

func (r *aiWorkflowRepository) Find(db *gorm.DB, cnd *sqls.Cnd) (list []models.AIWorkflow) {
	cnd.Find(db, &list)
	return
}

func (r *aiWorkflowRepository) FindPageByParams(db *gorm.DB, params *params.QueryParams) (list []models.AIWorkflow, paging *sqls.Paging) {
	return r.FindPageByCnd(db, &params.Cnd)
}

func (r *aiWorkflowRepository) FindPageByCnd(db *gorm.DB, cnd *sqls.Cnd) (list []models.AIWorkflow, paging *sqls.Paging) {
	cnd.Find(db, &list)
	count := cnd.Count(db, &models.AIWorkflow{})
	paging = &sqls.Paging{
		Page:  cnd.Paging.Page,
		Limit: cnd.Paging.Limit,
		Total: count,
	}
	return
}

func (r *aiWorkflowRepository) Create(db *gorm.DB, t *models.AIWorkflow) error {
	return db.Create(t).Error
}

func (r *aiWorkflowRepository) Updates(db *gorm.DB, id int64, columns map[string]interface{}) error {
	return db.Model(&models.AIWorkflow{}).Where("id = ?", id).Updates(columns).Error
}
