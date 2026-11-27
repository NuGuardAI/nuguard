package services

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/utils"
	"agent-desk/internal/repositories"
	"strings"
	"time"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
)

var QuickReplyService = newQuickReplyService()

func newQuickReplyService() *quickReplyService {
	return &quickReplyService{}
}

type quickReplyService struct {
}

func (s *quickReplyService) Get(id int64) *models.QuickReply {
	return repositories.QuickReplyRepository.Get(sqls.DB(), id)
}

func (s *quickReplyService) Take(where ...interface{}) *models.QuickReply {
	return repositories.QuickReplyRepository.Take(sqls.DB(), where...)
}

func (s *quickReplyService) Find(cnd *sqls.Cnd) []models.QuickReply {
	return repositories.QuickReplyRepository.Find(sqls.DB(), cnd)
}

func (s *quickReplyService) FindOne(cnd *sqls.Cnd) *models.QuickReply {
	return repositories.QuickReplyRepository.FindOne(sqls.DB(), cnd)
}

func (s *quickReplyService) FindPageByParams(params *params.QueryParams) (list []models.QuickReply, paging *sqls.Paging) {
	return repositories.QuickReplyRepository.FindPageByParams(sqls.DB(), params)
}

func (s *quickReplyService) FindPageByCnd(cnd *sqls.Cnd) (list []models.QuickReply, paging *sqls.Paging) {
	return repositories.QuickReplyRepository.FindPageByCnd(sqls.DB(), cnd)
}

func (s *quickReplyService) Count(cnd *sqls.Cnd) int64 {
	return repositories.QuickReplyRepository.Count(sqls.DB(), cnd)
}

func (s *quickReplyService) Create(t *models.QuickReply) error {
	return repositories.QuickReplyRepository.Create(sqls.DB(), t)
}

func (s *quickReplyService) Update(t *models.QuickReply) error {
	return repositories.QuickReplyRepository.Update(sqls.DB(), t)
}

func (s *quickReplyService) Updates(id int64, columns map[string]interface{}) error {
	return repositories.QuickReplyRepository.Updates(sqls.DB(), id, columns)
}

func (s *quickReplyService) UpdateColumn(id int64, name string, value interface{}) error {
	return repositories.QuickReplyRepository.UpdateColumn(sqls.DB(), id, name, value)
}

func (s *quickReplyService) Delete(id int64) {
	repositories.QuickReplyRepository.Delete(sqls.DB(), id)
}

func (s *quickReplyService) CreateQuickReply(req request.CreateQuickReplyRequest, operator *dto.AuthPrincipal) (*models.QuickReply, error) {
	if operator == nil {
		return nil, errorsx.UnauthorizedI18n("error.auth.expired")
	}
	title := strings.TrimSpace(req.Title)
	content := strings.TrimSpace(req.Content)
	if title == "" || content == "" {
		return nil, errorsx.InvalidParamI18n("error.e0240")
	}
	item := &models.QuickReply{
		GroupName:   strings.TrimSpace(req.GroupName),
		Title:       title,
		Content:     content,
		Status:      req.Status,
		SortNo:      req.SortNo,
		AuditFields: utils.BuildAuditFields(operator),
	}
	if err := s.Create(item); err != nil {
		return nil, err
	}
	return item, nil
}

func (s *quickReplyService) UpdateQuickReply(req request.UpdateQuickReplyRequest, operator *dto.AuthPrincipal) error {
	if operator == nil {
		return errorsx.UnauthorizedI18n("error.auth.expired")
	}
	item := s.Get(req.ID)
	if item == nil {
		return errorsx.InvalidParamI18n("error.e0203")
	}
	return s.Updates(req.ID, map[string]any{
		"group_name":       strings.TrimSpace(req.GroupName),
		"title":            strings.TrimSpace(req.Title),
		"content":          strings.TrimSpace(req.Content),
		"status":           req.Status,
		"sort_no":          req.SortNo,
		"update_user_id":   operator.UserID,
		"update_user_name": operator.Username,
		"updated_at":       time.Now(),
	})
}

func (s *quickReplyService) DeleteQuickReply(id int64) error {
	item := s.Get(id)
	if item == nil {
		return errorsx.InvalidParamI18n("error.e0203")
	}
	s.Delete(id)
	return nil
}
