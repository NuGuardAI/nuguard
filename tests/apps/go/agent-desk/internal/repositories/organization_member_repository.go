package repositories

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

var OrganizationMemberRepository = newOrganizationMemberRepository()

func newOrganizationMemberRepository() *organizationMemberRepository {
	return &organizationMemberRepository{}
}

type organizationMemberRepository struct{}

func (r *organizationMemberRepository) GetByOrgAndUser(db *gorm.DB, orgID, userID int64) *models.OrganizationMember {
	ret := &models.OrganizationMember{}
	if err := db.First(ret, "organization_id = ? AND user_id = ?", orgID, userID).Error; err != nil {
		return nil
	}
	return ret
}

func (r *organizationMemberRepository) Get(db *gorm.DB, id int64) *models.OrganizationMember {
	ret := &models.OrganizationMember{}
	if err := db.First(ret, "id = ?", id).Error; err != nil {
		return nil
	}
	return ret
}

func (r *organizationMemberRepository) Take(db *gorm.DB, where ...interface{}) *models.OrganizationMember {
	ret := &models.OrganizationMember{}
	if err := db.Take(ret, where...).Error; err != nil {
		return nil
	}
	return ret
}

func (r *organizationMemberRepository) Find(db *gorm.DB, cnd *sqls.Cnd) (list []models.OrganizationMember) {
	cnd.Find(db, &list)
	return
}

func (r *organizationMemberRepository) FindOne(db *gorm.DB, cnd *sqls.Cnd) *models.OrganizationMember {
	ret := &models.OrganizationMember{}
	if err := cnd.FindOne(db, &ret); err != nil {
		return nil
	}
	return ret
}

func (r *organizationMemberRepository) FindPageByParams(db *gorm.DB, params *params.QueryParams) (list []models.OrganizationMember, paging *sqls.Paging) {
	return r.FindPageByCnd(db, &params.Cnd)
}

func (r *organizationMemberRepository) FindPageByCnd(db *gorm.DB, cnd *sqls.Cnd) (list []models.OrganizationMember, paging *sqls.Paging) {
	cnd.Find(db, &list)
	count := cnd.Count(db, &models.OrganizationMember{})

	paging = &sqls.Paging{
		Page:  cnd.Paging.Page,
		Limit: cnd.Paging.Limit,
		Total: count,
	}
	return
}

func (r *organizationMemberRepository) Create(db *gorm.DB, t *models.OrganizationMember) error {
	return db.Create(t).Error
}

func (r *organizationMemberRepository) Update(db *gorm.DB, t *models.OrganizationMember) error {
	return db.Save(t).Error
}

func (r *organizationMemberRepository) Updates(db *gorm.DB, id int64, columns map[string]interface{}) error {
	return db.Model(&models.OrganizationMember{}).Where("id = ?", id).Updates(columns).Error
}

func (r *organizationMemberRepository) UpdateColumn(db *gorm.DB, id int64, name string, value interface{}) error {
	return db.Model(&models.OrganizationMember{}).Where("id = ?", id).UpdateColumn(name, value).Error
}

func (r *organizationMemberRepository) Delete(db *gorm.DB, id int64) error {
	return db.Delete(&models.OrganizationMember{}, "id = ?", id).Error
}
