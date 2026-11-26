package services

import (
	"agent-desk/internal/models"
	"agent-desk/internal/repositories"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
)

var TicketProgressService = newTicketProgressService()

func newTicketProgressService() *ticketProgressService {
	return &ticketProgressService{}
}

type ticketProgressService struct {
}

func (s *ticketProgressService) Get(id int64) *models.TicketProgress {
	return repositories.TicketProgressRepository.Get(sqls.DB(), id)
}

func (s *ticketProgressService) Take(where ...any) *models.TicketProgress {
	return repositories.TicketProgressRepository.Take(sqls.DB(), where...)
}

func (s *ticketProgressService) Find(cnd *sqls.Cnd) []models.TicketProgress {
	return repositories.TicketProgressRepository.Find(sqls.DB(), cnd)
}

func (s *ticketProgressService) FindOne(cnd *sqls.Cnd) *models.TicketProgress {
	return repositories.TicketProgressRepository.FindOne(sqls.DB(), cnd)
}

func (s *ticketProgressService) FindPageByParams(params *params.QueryParams) (list []models.TicketProgress, paging *sqls.Paging) {
	return repositories.TicketProgressRepository.FindPageByParams(sqls.DB(), params)
}

func (s *ticketProgressService) FindPageByCnd(cnd *sqls.Cnd) (list []models.TicketProgress, paging *sqls.Paging) {
	return repositories.TicketProgressRepository.FindPageByCnd(sqls.DB(), cnd)
}

func (s *ticketProgressService) Count(cnd *sqls.Cnd) int64 {
	return repositories.TicketProgressRepository.Count(sqls.DB(), cnd)
}

func (s *ticketProgressService) Create(t *models.TicketProgress) error {
	return repositories.TicketProgressRepository.Create(sqls.DB(), t)
}

func (s *ticketProgressService) Update(t *models.TicketProgress) error {
	return repositories.TicketProgressRepository.Update(sqls.DB(), t)
}

func (s *ticketProgressService) Updates(id int64, columns map[string]any) error {
	return repositories.TicketProgressRepository.Updates(sqls.DB(), id, columns)
}

func (s *ticketProgressService) UpdateColumn(id int64, name string, value any) error {
	return repositories.TicketProgressRepository.UpdateColumn(sqls.DB(), id, name, value)
}

func (s *ticketProgressService) Delete(id int64) {
	repositories.TicketProgressRepository.Delete(sqls.DB(), id)
}
