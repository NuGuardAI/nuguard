package repositories

import (
	"agent-desk/internal/models"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

var TicketProgressRepository = newTicketProgressRepository()

func newTicketProgressRepository() *ticketProgressRepository {
	return &ticketProgressRepository{}
}

type ticketProgressRepository struct {
}

func (r *ticketProgressRepository) Get(db *gorm.DB, id int64) *models.TicketProgress {
	ret := &models.TicketProgress{}
	if err := db.First(ret, "id = ?", id).Error; err != nil {
		return nil
	}
	return ret
}

func (r *ticketProgressRepository) Take(db *gorm.DB, where ...any) *models.TicketProgress {
	ret := &models.TicketProgress{}
	if err := db.Take(ret, where...).Error; err != nil {
		return nil
	}
	return ret
}

func (r *ticketProgressRepository) Find(db *gorm.DB, cnd *sqls.Cnd) (list []models.TicketProgress) {
	cnd.Find(db, &list)
	return
}

func (r *ticketProgressRepository) FindOne(db *gorm.DB, cnd *sqls.Cnd) *models.TicketProgress {
	ret := &models.TicketProgress{}
	if err := cnd.FindOne(db, &ret); err != nil {
		return nil
	}
	return ret
}

func (r *ticketProgressRepository) FindPageByParams(db *gorm.DB, params *params.QueryParams) (list []models.TicketProgress, paging *sqls.Paging) {
	return r.FindPageByCnd(db, &params.Cnd)
}

func (r *ticketProgressRepository) FindPageByCnd(db *gorm.DB, cnd *sqls.Cnd) (list []models.TicketProgress, paging *sqls.Paging) {
	cnd.Find(db, &list)
	count := cnd.Count(db, &models.TicketProgress{})

	paging = &sqls.Paging{
		Page:  cnd.Paging.Page,
		Limit: cnd.Paging.Limit,
		Total: count,
	}
	return
}

func (r *ticketProgressRepository) FindBySql(db *gorm.DB, sqlStr string, paramArr ...any) (list []models.TicketProgress) {
	db.Raw(sqlStr, paramArr...).Scan(&list)
	return
}

func (r *ticketProgressRepository) CountBySql(db *gorm.DB, sqlStr string, paramArr ...any) (count int64) {
	db.Raw(sqlStr, paramArr...).Count(&count)
	return
}

func (r *ticketProgressRepository) Count(db *gorm.DB, cnd *sqls.Cnd) int64 {
	return cnd.Count(db, &models.TicketProgress{})
}

func (r *ticketProgressRepository) Create(db *gorm.DB, t *models.TicketProgress) (err error) {
	err = db.Create(t).Error
	return
}

func (r *ticketProgressRepository) Update(db *gorm.DB, t *models.TicketProgress) (err error) {
	err = db.Save(t).Error
	return
}

func (r *ticketProgressRepository) Updates(db *gorm.DB, id int64, columns map[string]any) (err error) {
	err = db.Model(&models.TicketProgress{}).Where("id = ?", id).Updates(columns).Error
	return
}

func (r *ticketProgressRepository) UpdateColumn(db *gorm.DB, id int64, name string, value any) (err error) {
	err = db.Model(&models.TicketProgress{}).Where("id = ?", id).UpdateColumn(name, value).Error
	return
}

func (r *ticketProgressRepository) Delete(db *gorm.DB, id int64) {
	db.Delete(&models.TicketProgress{}, "id = ?", id)
}
