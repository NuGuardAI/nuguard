package services

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/utils"
	"agent-desk/internal/repositories"
	"strings"
	"time"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
)

var TagService = newTagService()

func newTagService() *tagService {
	return &tagService{}
}

type tagService struct {
}

func (s *tagService) Get(id int64) *models.Tag {
	return repositories.TagRepository.Get(sqls.DB(), id)
}

func (s *tagService) Take(where ...interface{}) *models.Tag {
	return repositories.TagRepository.Take(sqls.DB(), where...)
}

func (s *tagService) Find(cnd *sqls.Cnd) []models.Tag {
	return repositories.TagRepository.Find(sqls.DB(), cnd)
}

func (s *tagService) FindOne(cnd *sqls.Cnd) *models.Tag {
	return repositories.TagRepository.FindOne(sqls.DB(), cnd)
}

func (s *tagService) FindPageByParams(params *params.QueryParams) (list []models.Tag, paging *sqls.Paging) {
	return repositories.TagRepository.FindPageByParams(sqls.DB(), params)
}

func (s *tagService) FindPageByCnd(cnd *sqls.Cnd) (list []models.Tag, paging *sqls.Paging) {
	return repositories.TagRepository.FindPageByCnd(sqls.DB(), cnd)
}

func (s *tagService) Count(cnd *sqls.Cnd) int64 {
	return repositories.TagRepository.Count(sqls.DB(), cnd)
}

func (s *tagService) Create(t *models.Tag) error {
	return repositories.TagRepository.Create(sqls.DB(), t)
}

func (s *tagService) Update(t *models.Tag) error {
	return repositories.TagRepository.Update(sqls.DB(), t)
}

func (s *tagService) Updates(id int64, columns map[string]interface{}) error {
	return repositories.TagRepository.Updates(sqls.DB(), id, columns)
}

func (s *tagService) UpdateColumn(id int64, name string, value interface{}) error {
	return repositories.TagRepository.UpdateColumn(sqls.DB(), id, name, value)
}

func (s *tagService) Delete(id int64) {
	repositories.TagRepository.Delete(sqls.DB(), id)
}

func (s *tagService) GetChildren(parentID int64) []models.Tag {
	return s.Find(sqls.NewCnd().Eq("parent_id", parentID).Asc("sort_no").Asc("id"))
}

func (s *tagService) HasChildren(parentID int64) bool {
	return s.Count(sqls.NewCnd().Eq("parent_id", parentID)) > 0
}

func (s *tagService) FindByNameAndParentID(name string, parentID int64) *models.Tag {
	return s.FindOne(sqls.NewCnd().Eq("name", name).Eq("parent_id", parentID))
}

func (s *tagService) CreateTag(req request.CreateTagRequest, operator *dto.AuthPrincipal) (*models.Tag, error) {
	if operator == nil {
		return nil, errorsx.UnauthorizedI18n("error.auth.expired")
	}

	name := strings.TrimSpace(req.Name)
	if name == "" {
		return nil, errorsx.InvalidParamI18n("error.e0239")
	}

	if req.ParentID > 0 {
		parent := s.Get(req.ParentID)
		if parent == nil {
			return nil, errorsx.InvalidParamI18n("error.e0251")
		}
	}

	existing := s.FindByNameAndParentID(name, req.ParentID)
	if existing != nil {
		return nil, errorsx.InvalidParamI18n("error.e0141")
	}

	item := &models.Tag{
		ParentID:    req.ParentID,
		Name:        name,
		Remark:      strings.TrimSpace(req.Remark),
		Status:      enums.StatusOk,
		AuditFields: utils.BuildAuditFields(operator),
	}

	item.SortNo = s.NextSortNo(req.ParentID)
	if err := s.Create(item); err != nil {
		return nil, err
	}

	return item, nil
}

func (s *tagService) NextSortNo(parentID int64) int {
	if temp := s.FindOne(sqls.NewCnd().Eq("parent_id", parentID).Desc("sort_no").Desc("id")); temp != nil {
		return temp.SortNo + 1
	}
	return 1
}

func (s *tagService) UpdateTag(req request.UpdateTagRequest, operator *dto.AuthPrincipal) error {
	if operator == nil {
		return errorsx.UnauthorizedI18n("error.auth.expired")
	}

	item := s.Get(req.ID)
	if item == nil {
		return errorsx.InvalidParamI18n("error.e0238")
	}

	name := strings.TrimSpace(req.Name)
	if name == "" {
		return errorsx.InvalidParamI18n("error.e0239")
	}

	if req.ParentID > 0 {
		if req.ParentID == req.ID {
			return errorsx.InvalidParamI18n("error.e0083")
		}
		parent := s.Get(req.ParentID)
		if parent == nil {
			return errorsx.InvalidParamI18n("error.e0251")
		}
	}

	existing := s.FindByNameAndParentID(name, req.ParentID)
	if existing != nil && existing.ID != req.ID {
		return errorsx.InvalidParamI18n("error.e0141")
	}

	return s.Updates(req.ID, map[string]any{
		"parent_id":        req.ParentID,
		"name":             name,
		"remark":           strings.TrimSpace(req.Remark),
		"update_user_id":   operator.UserID,
		"update_user_name": operator.Username,
		"updated_at":       time.Now(),
	})
}

func (s *tagService) UpdateSort(ids []int64) error {
	return sqls.WithTransaction(func(ctx *sqls.TxContext) error {
		for i, id := range ids {
			if err := repositories.TagRepository.UpdateColumn(ctx.Tx, id, "sort_no", i+1); err != nil {
				return err
			}
		}
		return nil
	})
}

func (s *tagService) DeleteTag(id int64) error {
	item := s.Get(id)
	if item == nil {
		return errorsx.InvalidParamI18n("error.e0238")
	}

	if s.HasChildren(id) {
		return errorsx.InvalidParamI18n("error.e0310")
	}
	if ConversationTagService.Take("tag_id = ?", id) != nil {
		return errorsx.InvalidParamI18n("error.e0311")
	}
	if TicketTagService.Take("tag_id = ?", id) != nil {
		return errorsx.InvalidParamI18n("error.e0312")
	}

	s.Delete(id)
	return nil
}

func (s *tagService) FindAll() []models.Tag {
	return s.Find(sqls.NewCnd().Asc("sort_no").Asc("id"))
}

func (s *tagService) GetSelfAndDescendantIDs(tagID int64) []int64 {
	if tagID <= 0 {
		return nil
	}

	allTags := s.FindAll()
	if len(allTags) == 0 {
		return nil
	}

	exists := false
	childrenMap := make(map[int64][]int64, len(allTags))
	for _, item := range allTags {
		if item.ID == tagID {
			exists = true
		}
		childrenMap[item.ParentID] = append(childrenMap[item.ParentID], item.ID)
	}
	if !exists {
		return nil
	}

	result := make([]int64, 0, 8)
	visited := make(map[int64]bool, len(allTags))
	var walk func(id int64)
	walk = func(id int64) {
		if visited[id] {
			return
		}
		visited[id] = true
		result = append(result, id)
		for _, childID := range childrenMap[id] {
			walk(childID)
		}
	}
	walk(tagID)

	return result
}

func (s *tagService) UpdateStatus(id int64, status int, operator *dto.AuthPrincipal) error {
	if operator == nil {
		return errorsx.UnauthorizedI18n("error.auth.expired")
	}

	item := s.Get(id)
	if item == nil {
		return errorsx.InvalidParamI18n("error.e0238")
	}

	if status != int(enums.StatusOk) && status != int(enums.StatusDisabled) {
		return errorsx.InvalidParamI18n("error.e0254")
	}

	now := time.Now()
	return s.Updates(id, map[string]any{
		"status":           status,
		"update_user_id":   operator.UserID,
		"update_user_name": operator.Username,
		"updated_at":       now,
	})
}
