package services

import (
	"encoding/json"
	"strings"
	"time"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/utils"
	"agent-desk/internal/repositories"

	"github.com/mlogclub/simple/sqls"
)

var TicketViewService = newTicketViewService()

func newTicketViewService() *ticketViewService {
	return &ticketViewService{}
}

type ticketViewService struct {
}

func (s *ticketViewService) Get(id int64) *models.TicketView {
	return repositories.TicketViewRepository.Get(sqls.DB(), id)
}

func (s *ticketViewService) Find(cnd *sqls.Cnd) []models.TicketView {
	return repositories.TicketViewRepository.Find(sqls.DB(), cnd)
}

func (s *ticketViewService) ListByUser(userID int64) []models.TicketView {
	if userID <= 0 {
		return nil
	}
	return s.Find(sqls.NewCnd().Eq("user_id", userID).Asc("sort_no").Desc("id"))
}

func (s *ticketViewService) Save(req request.SaveTicketViewRequest, operator *dto.AuthPrincipal) (*models.TicketView, error) {
	if operator == nil {
		return nil, errorsx.UnauthorizedI18n("error.auth.expired")
	}
	name := strings.TrimSpace(req.Name)
	if name == "" {
		return nil, errorsx.InvalidParamI18n("error.e0303")
	}
	filtersJSON, err := json.Marshal(req.Filters)
	if err != nil {
		return nil, errorsx.InvalidParamI18n("error.e0304")
	}
	now := time.Now()
	if req.ID > 0 {
		item := repositories.TicketViewRepository.Get(sqls.DB(), req.ID)
		if item == nil || item.UserID != operator.UserID {
			return nil, errorsx.InvalidParamI18n("error.e0302")
		}
		if err := repositories.TicketViewRepository.Updates(sqls.DB(), req.ID, map[string]any{
			"name":             name,
			"filters_json":     string(filtersJSON),
			"update_user_id":   operator.UserID,
			"update_user_name": operator.Username,
			"updated_at":       now,
		}); err != nil {
			return nil, err
		}
		return repositories.TicketViewRepository.Get(sqls.DB(), req.ID), nil
	}
	item := &models.TicketView{
		UserID:      operator.UserID,
		Name:        name,
		FiltersJSON: string(filtersJSON),
		AuditFields: utils.BuildAuditFields(operator),
	}
	if err := repositories.TicketViewRepository.Create(sqls.DB(), item); err != nil {
		return nil, err
	}
	return item, nil
}

func (s *ticketViewService) Delete(id int64, operator *dto.AuthPrincipal) error {
	if operator == nil {
		return errorsx.UnauthorizedI18n("error.auth.expired")
	}
	item := repositories.TicketViewRepository.Get(sqls.DB(), id)
	if item == nil || item.UserID != operator.UserID {
		return errorsx.InvalidParamI18n("error.e0302")
	}
	return repositories.TicketViewRepository.Delete(sqls.DB(), id)
}
