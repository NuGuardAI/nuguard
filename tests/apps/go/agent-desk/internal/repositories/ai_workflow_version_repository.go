package repositories

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

var AIWorkflowVersionRepository = newAIWorkflowVersionRepository()

func newAIWorkflowVersionRepository() *aiWorkflowVersionRepository {
	return &aiWorkflowVersionRepository{}
}

type aiWorkflowVersionRepository struct{}

func (r *aiWorkflowVersionRepository) Get(db *gorm.DB, id int64) *models.AIWorkflowVersion {
	ret := &models.AIWorkflowVersion{}
	if err := db.First(ret, "id = ?", id).Error; err != nil {
		return nil
	}
	return ret
}

func (r *aiWorkflowVersionRepository) Find(db *gorm.DB, cnd *sqls.Cnd) (list []models.AIWorkflowVersion) {
	cnd.Find(db, &list)
	return
}

func (r *aiWorkflowVersionRepository) FindPageByParams(db *gorm.DB, params *params.QueryParams) (list []models.AIWorkflowVersion, paging *sqls.Paging) {
	return r.FindPageByCnd(db, &params.Cnd)
}

func (r *aiWorkflowVersionRepository) FindPageByCnd(db *gorm.DB, cnd *sqls.Cnd) (list []models.AIWorkflowVersion, paging *sqls.Paging) {
	cnd.Find(db, &list)
	count := cnd.Count(db, &models.AIWorkflowVersion{})
	paging = &sqls.Paging{
		Page:  cnd.Paging.Page,
		Limit: cnd.Paging.Limit,
		Total: count,
	}
	return
}

func (r *aiWorkflowVersionRepository) Create(db *gorm.DB, t *models.AIWorkflowVersion) error {
	return db.Create(t).Error
}

func (r *aiWorkflowVersionRepository) MaxVersionByWorkflowID(db *gorm.DB, workflowID int64) int {
	var maxVersion int
	db.Model(&models.AIWorkflowVersion{}).
		Where("workflow_id = ?", workflowID).
		Select("COALESCE(MAX(version), 0)").
		Scan(&maxVersion)
	return maxVersion
}
