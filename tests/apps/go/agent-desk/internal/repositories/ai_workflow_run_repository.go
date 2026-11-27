package repositories

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

var AIWorkflowRunRepository = newAIWorkflowRunRepository()

func newAIWorkflowRunRepository() *aiWorkflowRunRepository {
	return &aiWorkflowRunRepository{}
}

type aiWorkflowRunRepository struct{}

func (r *aiWorkflowRunRepository) Get(db *gorm.DB, id int64) *models.AIWorkflowRun {
	ret := &models.AIWorkflowRun{}
	if err := db.First(ret, "id = ?", id).Error; err != nil {
		return nil
	}
	return ret
}

func (r *aiWorkflowRunRepository) Find(db *gorm.DB, cnd *sqls.Cnd) (list []models.AIWorkflowRun) {
	cnd.Find(db, &list)
	return
}

func (r *aiWorkflowRunRepository) FindPageByParams(db *gorm.DB, params *params.QueryParams) (list []models.AIWorkflowRun, paging *sqls.Paging) {
	return r.FindPageByCnd(db, &params.Cnd)
}

func (r *aiWorkflowRunRepository) FindPageByCnd(db *gorm.DB, cnd *sqls.Cnd) (list []models.AIWorkflowRun, paging *sqls.Paging) {
	cnd.Find(db, &list)
	count := cnd.Count(db, &models.AIWorkflowRun{})
	paging = &sqls.Paging{
		Page:  cnd.Paging.Page,
		Limit: cnd.Paging.Limit,
		Total: count,
	}
	return
}

func (r *aiWorkflowRunRepository) Create(db *gorm.DB, t *models.AIWorkflowRun) error {
	return db.Create(t).Error
}

func (r *aiWorkflowRunRepository) Updates(db *gorm.DB, id int64, columns map[string]interface{}) error {
	return db.Model(&models.AIWorkflowRun{}).Where("id = ?", id).Updates(columns).Error
}
