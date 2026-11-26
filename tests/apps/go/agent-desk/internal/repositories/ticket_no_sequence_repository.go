package repositories

import (
	"agent-desk/internal/models"
	"errors"
	"time"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

var TicketNoSequenceRepository = newTicketNoSequenceRepository()

func newTicketNoSequenceRepository() *ticketNoSequenceRepository {
	return &ticketNoSequenceRepository{}
}

type ticketNoSequenceRepository struct{}

func (r *ticketNoSequenceRepository) Get(db *gorm.DB, id int64) *models.TicketNoSequence {
	ret := &models.TicketNoSequence{}
	if err := db.First(ret, "id = ?", id).Error; err != nil {
		return nil
	}
	return ret
}

func (r *ticketNoSequenceRepository) Take(db *gorm.DB, where ...any) *models.TicketNoSequence {
	ret := &models.TicketNoSequence{}
	if err := db.Take(ret, where...).Error; err != nil {
		return nil
	}
	return ret
}

func (r *ticketNoSequenceRepository) Find(db *gorm.DB, cnd *sqls.Cnd) (list []models.TicketNoSequence) {
	cnd.Find(db, &list)
	return
}

func (r *ticketNoSequenceRepository) FindOne(db *gorm.DB, cnd *sqls.Cnd) *models.TicketNoSequence {
	ret := &models.TicketNoSequence{}
	if err := cnd.FindOne(db, &ret); err != nil {
		return nil
	}
	return ret
}

func (r *ticketNoSequenceRepository) FindPageByParams(db *gorm.DB, params *params.QueryParams) (list []models.TicketNoSequence, paging *sqls.Paging) {
	return r.FindPageByCnd(db, &params.Cnd)
}

func (r *ticketNoSequenceRepository) FindPageByCnd(db *gorm.DB, cnd *sqls.Cnd) (list []models.TicketNoSequence, paging *sqls.Paging) {
	cnd.Find(db, &list)
	count := cnd.Count(db, &models.TicketNoSequence{})

	paging = &sqls.Paging{
		Page:  cnd.Paging.Page,
		Limit: cnd.Paging.Limit,
		Total: count,
	}
	return
}

func (r *ticketNoSequenceRepository) Count(db *gorm.DB, cnd *sqls.Cnd) int64 {
	return cnd.Count(db, &models.TicketNoSequence{})
}

func (r *ticketNoSequenceRepository) GetByDateKey(db *gorm.DB, dateKey string) *models.TicketNoSequence {
	ret := &models.TicketNoSequence{}
	if err := db.Take(ret, "date_key = ?", dateKey).Error; err != nil {
		return nil
	}
	return ret
}

func (r *ticketNoSequenceRepository) GetByDateKeyForUpdate(db *gorm.DB, dateKey string) (*models.TicketNoSequence, error) {
	ret := &models.TicketNoSequence{}
	err := db.Clauses(clause.Locking{Strength: "UPDATE"}).Take(ret, "date_key = ?", dateKey).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return ret, nil
}

func (r *ticketNoSequenceRepository) Create(db *gorm.DB, t *models.TicketNoSequence) error {
	return db.Create(t).Error
}

func (r *ticketNoSequenceRepository) Update(db *gorm.DB, t *models.TicketNoSequence) error {
	return db.Save(t).Error
}

func (r *ticketNoSequenceRepository) Updates(db *gorm.DB, id int64, columns map[string]any) error {
	return db.Model(&models.TicketNoSequence{}).Where("id = ?", id).Updates(columns).Error
}

func (r *ticketNoSequenceRepository) UpdateColumn(db *gorm.DB, id int64, name string, value any) error {
	return db.Model(&models.TicketNoSequence{}).Where("id = ?", id).UpdateColumn(name, value).Error
}

func (r *ticketNoSequenceRepository) Delete(db *gorm.DB, id int64) {
	db.Delete(&models.TicketNoSequence{}, "id = ?", id)
}

func (r *ticketNoSequenceRepository) UpdateNextSeq(db *gorm.DB, id int64, currentSeq, nextSeq int64, updatedAt time.Time) (bool, error) {
	result := db.Model(&models.TicketNoSequence{}).
		Where("id = ? AND next_seq = ?", id, currentSeq).
		Updates(map[string]any{
			"next_seq":   nextSeq,
			"updated_at": updatedAt,
		})
	return result.RowsAffected == 1, result.Error
}
