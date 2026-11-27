package services

import (
	"strings"
	"time"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/utils"
	"agent-desk/internal/repositories"

	"github.com/mlogclub/simple/sqls"
)

var KnowledgeDirectoryService = newKnowledgeDirectoryService()

func newKnowledgeDirectoryService() *knowledgeDirectoryService {
	return &knowledgeDirectoryService{}
}

type knowledgeDirectoryService struct {
}

func (s *knowledgeDirectoryService) Get(id int64) *models.KnowledgeDirectory {
	return repositories.KnowledgeDirectoryRepository.Get(sqls.DB(), id)
}

func (s *knowledgeDirectoryService) Find(cnd *sqls.Cnd) []models.KnowledgeDirectory {
	return repositories.KnowledgeDirectoryRepository.Find(sqls.DB(), cnd)
}

func (s *knowledgeDirectoryService) FindAllByKnowledgeBaseID(knowledgeBaseID int64) []models.KnowledgeDirectory {
	return s.Find(sqls.NewCnd().Eq("knowledge_base_id", knowledgeBaseID).Asc("parent_id").Asc("sort_no").Asc("id"))
}

func (s *knowledgeDirectoryService) Count(cnd *sqls.Cnd) int64 {
	return repositories.KnowledgeDirectoryRepository.Count(sqls.DB(), cnd)
}

func (s *knowledgeDirectoryService) CreateDirectory(req request.CreateKnowledgeDirectoryRequest, operator *dto.AuthPrincipal) (*models.KnowledgeDirectory, error) {
	if operator == nil {
		return nil, errorsx.UnauthorizedI18n("error.auth.expired")
	}
	name := strings.TrimSpace(req.Name)
	if name == "" {
		return nil, errorsx.InvalidParamI18n("error.e0275")
	}
	if req.KnowledgeBaseID <= 0 || KnowledgeBaseService.Get(req.KnowledgeBaseID) == nil {
		return nil, errorsx.InvalidParamI18n("error.e0283")
	}
	if err := s.validateParent(req.KnowledgeBaseID, req.ParentID, 0); err != nil {
		return nil, err
	}
	if existing := s.findByName(req.KnowledgeBaseID, req.ParentID, name); existing != nil {
		return nil, errorsx.InvalidParamI18n("error.e0142")
	}
	item := &models.KnowledgeDirectory{
		KnowledgeBaseID: req.KnowledgeBaseID,
		ParentID:        req.ParentID,
		Name:            name,
		SortNo:          s.NextSortNo(req.KnowledgeBaseID, req.ParentID),
		Status:          enums.StatusOk,
		Remark:          strings.TrimSpace(req.Remark),
		AuditFields:     utils.BuildAuditFields(operator),
	}
	if err := repositories.KnowledgeDirectoryRepository.Create(sqls.DB(), item); err != nil {
		return nil, err
	}
	return item, nil
}

func (s *knowledgeDirectoryService) UpdateDirectory(req request.UpdateKnowledgeDirectoryRequest, operator *dto.AuthPrincipal) error {
	if operator == nil {
		return errorsx.UnauthorizedI18n("error.auth.expired")
	}
	item := s.Get(req.ID)
	if item == nil {
		return errorsx.InvalidParamI18n("error.e0273")
	}
	name := strings.TrimSpace(req.Name)
	if name == "" {
		return errorsx.InvalidParamI18n("error.e0275")
	}
	if req.KnowledgeBaseID <= 0 {
		req.KnowledgeBaseID = item.KnowledgeBaseID
	}
	if req.KnowledgeBaseID != item.KnowledgeBaseID {
		return errorsx.InvalidParamI18n("error.e0274")
	}
	if err := s.validateParent(item.KnowledgeBaseID, req.ParentID, req.ID); err != nil {
		return err
	}
	if req.ParentID > 0 && s.Count(sqls.NewCnd().Eq("parent_id", req.ID)) > 0 {
		return errorsx.InvalidParamI18n("error.e0152")
	}
	if existing := s.findByName(item.KnowledgeBaseID, req.ParentID, name); existing != nil && existing.ID != req.ID {
		return errorsx.InvalidParamI18n("error.e0142")
	}
	return repositories.KnowledgeDirectoryRepository.Updates(sqls.DB(), req.ID, map[string]any{
		"parent_id":        req.ParentID,
		"name":             name,
		"remark":           strings.TrimSpace(req.Remark),
		"update_user_id":   operator.UserID,
		"update_user_name": operator.Username,
		"updated_at":       time.Now(),
	})
}

func (s *knowledgeDirectoryService) DeleteDirectory(id int64) error {
	item := s.Get(id)
	if item == nil {
		return errorsx.InvalidParamI18n("error.e0273")
	}
	if s.Count(sqls.NewCnd().Eq("parent_id", id)) > 0 {
		return errorsx.InvalidParamI18n("error.e0316")
	}
	if repositories.KnowledgeDocumentRepository.CountActiveByDirectoryID(sqls.DB(), id) > 0 {
		return errorsx.InvalidParamI18n("error.e0317")
	}
	if repositories.KnowledgeFAQRepository.CountActiveByDirectoryID(sqls.DB(), id) > 0 {
		return errorsx.InvalidParamI18n("error.e0315")
	}
	return repositories.KnowledgeDirectoryRepository.Delete(sqls.DB(), id)
}

func (s *knowledgeDirectoryService) UpdateSort(knowledgeBaseID int64, parentID int64, ids []int64) error {
	return sqls.WithTransaction(func(ctx *sqls.TxContext) error {
		for i, id := range ids {
			item := repositories.KnowledgeDirectoryRepository.Get(ctx.Tx, id)
			if item == nil {
				return errorsx.InvalidParamI18n("error.e0273")
			}
			if item.KnowledgeBaseID != knowledgeBaseID || item.ParentID != parentID {
				return errorsx.InvalidParamI18n("error.e0140")
			}
			if err := repositories.KnowledgeDirectoryRepository.UpdateColumn(ctx.Tx, id, "sort_no", i+1); err != nil {
				return err
			}
		}
		return nil
	})
}

func (s *knowledgeDirectoryService) RequireUsableDirectory(knowledgeBaseID int64, directoryID int64) (*models.KnowledgeDirectory, error) {
	if directoryID <= 0 {
		return nil, nil
	}
	item := s.Get(directoryID)
	if item == nil {
		return nil, errorsx.InvalidParamI18n("error.e0287")
	}
	if item.KnowledgeBaseID != knowledgeBaseID {
		return nil, errorsx.InvalidParamI18n("error.e0288")
	}
	if item.Status != enums.StatusOk {
		return nil, errorsx.InvalidParamI18n("error.e0286")
	}
	return item, nil
}

func (s *knowledgeDirectoryService) NextSortNo(knowledgeBaseID int64, parentID int64) int {
	if temp := repositories.KnowledgeDirectoryRepository.FindOne(sqls.DB(), sqls.NewCnd().Eq("knowledge_base_id", knowledgeBaseID).Eq("parent_id", parentID).Desc("sort_no").Desc("id")); temp != nil {
		return temp.SortNo + 1
	}
	return 1
}

func (s *knowledgeDirectoryService) PathMap(knowledgeBaseID int64) map[int64]string {
	items := s.FindAllByKnowledgeBaseID(knowledgeBaseID)
	byID := make(map[int64]models.KnowledgeDirectory, len(items))
	for _, item := range items {
		byID[item.ID] = item
	}
	ret := make(map[int64]string, len(items))
	for _, item := range items {
		if item.ParentID > 0 {
			if parent, ok := byID[item.ParentID]; ok {
				ret[item.ID] = parent.Name + " / " + item.Name
				continue
			}
		}
		ret[item.ID] = item.Name
	}
	return ret
}

func (s *knowledgeDirectoryService) validateParent(knowledgeBaseID int64, parentID int64, selfID int64) error {
	if parentID <= 0 {
		return nil
	}
	if parentID == selfID {
		return errorsx.InvalidParamI18n("error.e0084")
	}
	parent := s.Get(parentID)
	if parent == nil {
		return errorsx.InvalidParamI18n("error.e0252")
	}
	if parent.KnowledgeBaseID != knowledgeBaseID {
		return errorsx.InvalidParamI18n("error.e0253")
	}
	if parent.ParentID > 0 {
		return errorsx.InvalidParamI18n("error.e0289")
	}
	return nil
}

func (s *knowledgeDirectoryService) findByName(knowledgeBaseID int64, parentID int64, name string) *models.KnowledgeDirectory {
	return repositories.KnowledgeDirectoryRepository.FindOne(sqls.DB(), sqls.NewCnd().Eq("knowledge_base_id", knowledgeBaseID).Eq("parent_id", parentID).Eq("name", name))
}
