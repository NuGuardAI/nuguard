package repositories

import (
	"agent-desk/internal/models"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

var KnowledgeDirectoryRepository = newKnowledgeDirectoryRepository()

func newKnowledgeDirectoryRepository() *knowledgeDirectoryRepository {
	return &knowledgeDirectoryRepository{}
}

type knowledgeDirectoryRepository struct {
}

func (r *knowledgeDirectoryRepository) Get(db *gorm.DB, id int64) *models.KnowledgeDirectory {
	ret := &models.KnowledgeDirectory{}
	if err := db.First(ret, "id = ?", id).Error; err != nil {
		return nil
	}
	return ret
}

func (r *knowledgeDirectoryRepository) Find(db *gorm.DB, cnd *sqls.Cnd) (list []models.KnowledgeDirectory) {
	cnd.Find(db, &list)
	return
}

func (r *knowledgeDirectoryRepository) FindOne(db *gorm.DB, cnd *sqls.Cnd) *models.KnowledgeDirectory {
	ret := &models.KnowledgeDirectory{}
	if err := cnd.FindOne(db, &ret); err != nil {
		return nil
	}
	return ret
}

func (r *knowledgeDirectoryRepository) Count(db *gorm.DB, cnd *sqls.Cnd) int64 {
	return cnd.Count(db, &models.KnowledgeDirectory{})
}

func (r *knowledgeDirectoryRepository) Create(db *gorm.DB, t *models.KnowledgeDirectory) error {
	return db.Create(t).Error
}

func (r *knowledgeDirectoryRepository) Updates(db *gorm.DB, id int64, columns map[string]any) error {
	return db.Model(&models.KnowledgeDirectory{}).Where("id = ?", id).Updates(columns).Error
}

func (r *knowledgeDirectoryRepository) UpdateColumn(db *gorm.DB, id int64, name string, value any) error {
	return db.Model(&models.KnowledgeDirectory{}).Where("id = ?", id).UpdateColumn(name, value).Error
}

func (r *knowledgeDirectoryRepository) Delete(db *gorm.DB, id int64) error {
	return db.Delete(&models.KnowledgeDirectory{}, "id = ?", id).Error
}
