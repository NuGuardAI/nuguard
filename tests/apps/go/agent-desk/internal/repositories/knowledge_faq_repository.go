package repositories

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

var KnowledgeFAQRepository = newKnowledgeFAQRepository()

func newKnowledgeFAQRepository() *knowledgeFAQRepository {
	return &knowledgeFAQRepository{}
}

type knowledgeFAQRepository struct{}

func (r *knowledgeFAQRepository) Get(db *gorm.DB, id int64) *models.KnowledgeFAQ {
	ret := &models.KnowledgeFAQ{}
	if err := db.First(ret, "id = ?", id).Error; err != nil {
		return nil
	}
	return ret
}

func (r *knowledgeFAQRepository) Find(db *gorm.DB, cnd *sqls.Cnd) (list []models.KnowledgeFAQ) {
	cnd.Find(db, &list)
	return
}

func (r *knowledgeFAQRepository) FindPageByCnd(db *gorm.DB, cnd *sqls.Cnd) (list []models.KnowledgeFAQ, paging *sqls.Paging) {
	cnd.Find(db, &list)
	count := cnd.Count(db, &models.KnowledgeFAQ{})
	paging = &sqls.Paging{
		Page:  cnd.Paging.Page,
		Limit: cnd.Paging.Limit,
		Total: count,
	}
	return
}

func (r *knowledgeFAQRepository) FindPageByParams(db *gorm.DB, queryParams *params.QueryParams) (list []models.KnowledgeFAQ, paging *sqls.Paging) {
	return r.FindPageByCnd(db, &queryParams.Cnd)
}

func (r *knowledgeFAQRepository) Count(db *gorm.DB, cnd *sqls.Cnd) int64 {
	return cnd.Count(db, &models.KnowledgeFAQ{})
}

func (r *knowledgeFAQRepository) CountActiveByDirectoryID(db *gorm.DB, directoryID int64) int64 {
	var count int64
	db.Model(&models.KnowledgeFAQ{}).
		Where("directory_id = ? AND status <> ?", directoryID, enums.StatusDeleted).
		Count(&count)
	return count
}

func (r *knowledgeFAQRepository) Create(db *gorm.DB, t *models.KnowledgeFAQ) error {
	return db.Create(t).Error
}

func (r *knowledgeFAQRepository) Updates(db *gorm.DB, id int64, columns map[string]any) error {
	return db.Model(&models.KnowledgeFAQ{}).Where("id = ?", id).Updates(columns).Error
}

func (r *knowledgeFAQRepository) Delete(db *gorm.DB, id int64) error {
	return db.Delete(&models.KnowledgeFAQ{}, "id = ?", id).Error
}

func (r *knowledgeFAQRepository) DeleteByKnowledgeBaseID(db *gorm.DB, knowledgeBaseID int64) error {
	return db.Delete(&models.KnowledgeFAQ{}, "knowledge_base_id = ?", knowledgeBaseID).Error
}

func (r *knowledgeFAQRepository) FindByIDs(db *gorm.DB, ids []int64) (list []models.KnowledgeFAQ) {
	if len(ids) == 0 {
		return nil
	}
	db.Where("id IN ?", ids).Find(&list)
	return
}

func (r *knowledgeFAQRepository) FindAllByKnowledgeBaseID(db *gorm.DB, knowledgeBaseID int64) (list []models.KnowledgeFAQ) {
	db.Where("knowledge_base_id = ? AND status <> ?", knowledgeBaseID, enums.StatusDeleted).Order("id DESC").Find(&list)
	return
}

func (r *knowledgeFAQRepository) FindByKnowledgeBaseIDAndQuestions(db *gorm.DB, knowledgeBaseID int64, questions []string) (list []models.KnowledgeFAQ) {
	if len(questions) == 0 {
		return nil
	}
	db.Where("knowledge_base_id = ? AND question IN ?", knowledgeBaseID, questions).Find(&list)
	return
}

func (r *knowledgeFAQRepository) CountByKnowledgeBaseID(db *gorm.DB, knowledgeBaseID int64) int64 {
	var count int64
	db.Model(&models.KnowledgeFAQ{}).Where("knowledge_base_id = ? AND status <> ?", knowledgeBaseID, enums.StatusDeleted).Count(&count)
	return count
}
