package repositories

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

var OrganizationRepository = newOrganizationRepository()

func newOrganizationRepository() *organizationRepository {
	return &organizationRepository{}
}

type organizationRepository struct{}

func (r *organizationRepository) GetByCode(db *gorm.DB, code string) *models.Organization {
	ret := &models.Organization{}
	if err := db.First(ret, "code = ?", code).Error; err != nil {
		return nil
	}
	return ret
}

func (r *organizationRepository) Get(db *gorm.DB, id int64) *models.Organization {
	ret := &models.Organization{}
	if err := db.First(ret, "id = ?", id).Error; err != nil {
		return nil
	}
	return ret
}

func (r *organizationRepository) Take(db *gorm.DB, where ...interface{}) *models.Organization {
	ret := &models.Organization{}
	if err := db.Take(ret, where...).Error; err != nil {
		return nil
	}
	return ret
}

func (r *organizationRepository) Find(db *gorm.DB, cnd *sqls.Cnd) (list []models.Organization) {
	cnd.Find(db, &list)
	return
}

func (r *organizationRepository) FindOne(db *gorm.DB, cnd *sqls.Cnd) *models.Organization {
	ret := &models.Organization{}
	if err := cnd.FindOne(db, &ret); err != nil {
		return nil
	}
	return ret
}

func (r *organizationRepository) FindPageByParams(db *gorm.DB, params *params.QueryParams) (list []models.Organization, paging *sqls.Paging) {
	return r.FindPageByCnd(db, &params.Cnd)
}

func (r *organizationRepository) FindPageByCnd(db *gorm.DB, cnd *sqls.Cnd) (list []models.Organization, paging *sqls.Paging) {
	cnd.Find(db, &list)
	count := cnd.Count(db, &models.Organization{})

	paging = &sqls.Paging{
		Page:  cnd.Paging.Page,
		Limit: cnd.Paging.Limit,
		Total: count,
	}
	return
}

func (r *organizationRepository) Create(db *gorm.DB, t *models.Organization) error {
	return db.Create(t).Error
}

func (r *organizationRepository) Update(db *gorm.DB, t *models.Organization) error {
	return db.Save(t).Error
}

func (r *organizationRepository) Updates(db *gorm.DB, id int64, columns map[string]interface{}) error {
	return db.Model(&models.Organization{}).Where("id = ?", id).Updates(columns).Error
}

func (r *organizationRepository) UpdateColumn(db *gorm.DB, id int64, name string, value interface{}) error {
	return db.Model(&models.Organization{}).Where("id = ?", id).UpdateColumn(name, value).Error
}

func (r *organizationRepository) Delete(db *gorm.DB, id int64) error {
	return db.Delete(&models.Organization{}, "id = ?", id).Error
}
