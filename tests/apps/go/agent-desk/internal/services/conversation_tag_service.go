package services

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/utils"
	"agent-desk/internal/repositories"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
)

var ConversationTagService = newConversationTagService()

func newConversationTagService() *conversationTagService {
	return &conversationTagService{}
}

type conversationTagService struct {
}

func (s *conversationTagService) Get(id int64) *models.ConversationTag {
	return repositories.ConversationTagRepository.Get(sqls.DB(), id)
}

func (s *conversationTagService) Take(where ...interface{}) *models.ConversationTag {
	return repositories.ConversationTagRepository.Take(sqls.DB(), where...)
}

func (s *conversationTagService) Find(cnd *sqls.Cnd) []models.ConversationTag {
	return repositories.ConversationTagRepository.Find(sqls.DB(), cnd)
}

func (s *conversationTagService) FindOne(cnd *sqls.Cnd) *models.ConversationTag {
	return repositories.ConversationTagRepository.FindOne(sqls.DB(), cnd)
}

func (s *conversationTagService) FindPageByParams(params *params.QueryParams) (list []models.ConversationTag, paging *sqls.Paging) {
	return repositories.ConversationTagRepository.FindPageByParams(sqls.DB(), params)
}

func (s *conversationTagService) FindPageByCnd(cnd *sqls.Cnd) (list []models.ConversationTag, paging *sqls.Paging) {
	return repositories.ConversationTagRepository.FindPageByCnd(sqls.DB(), cnd)
}

func (s *conversationTagService) Count(cnd *sqls.Cnd) int64 {
	return repositories.ConversationTagRepository.Count(sqls.DB(), cnd)
}

func (s *conversationTagService) Create(t *models.ConversationTag) error {
	return repositories.ConversationTagRepository.Create(sqls.DB(), t)
}

func (s *conversationTagService) Update(t *models.ConversationTag) error {
	return repositories.ConversationTagRepository.Update(sqls.DB(), t)
}

func (s *conversationTagService) Updates(id int64, columns map[string]interface{}) error {
	return repositories.ConversationTagRepository.Updates(sqls.DB(), id, columns)
}

func (s *conversationTagService) UpdateColumn(id int64, name string, value interface{}) error {
	return repositories.ConversationTagRepository.UpdateColumn(sqls.DB(), id, name, value)
}

func (s *conversationTagService) Delete(id int64) {
	repositories.ConversationTagRepository.Delete(sqls.DB(), id)
}

func (s *conversationTagService) IsExists(conversationID int64, tagID int64) bool {
	return repositories.ConversationTagRepository.FindOne(sqls.DB(), sqls.NewCnd().Where("conversation_id = ? AND tag_id = ?", conversationID, tagID)) != nil
}

func (s *conversationTagService) AddTag(req request.AddConversationTagRequest, operator *dto.AuthPrincipal) error {
	tag := TagService.Get(req.TagID)
	if tag == nil || tag.Status != enums.StatusOk {
		return errorsx.InvalidParamI18n("error.conversation.tagNotFound")
	}
	if s.IsExists(req.ConversationID, req.TagID) {
		return nil
	}
	return repositories.ConversationTagRepository.Create(sqls.DB(), &models.ConversationTag{
		ConversationID: req.ConversationID,
		TagID:          req.TagID,
		AuditFields:    utils.BuildAuditFields(operator),
	})
}

func (s *conversationTagService) RemoveTag(req request.RemoveConversationTagRequest) error {
	return sqls.DB().Where("conversation_id = ? AND tag_id = ?", req.ConversationID, req.TagID).Delete(&models.ConversationTag{}).Error
}
