package services

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/tracex"
	"agent-desk/internal/repositories"
	"strings"
	"time"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
)

var ConversationEventLogService = newConversationEventLogService()

func newConversationEventLogService() *conversationEventLogService {
	return &conversationEventLogService{}
}

type conversationEventLogService struct {
}

func (s *conversationEventLogService) Get(id int64) *models.ConversationEventLog {
	return repositories.ConversationEventLogRepository.Get(sqls.DB(), id)
}

func (s *conversationEventLogService) Take(where ...interface{}) *models.ConversationEventLog {
	return repositories.ConversationEventLogRepository.Take(sqls.DB(), where...)
}

func (s *conversationEventLogService) Find(cnd *sqls.Cnd) []models.ConversationEventLog {
	return repositories.ConversationEventLogRepository.Find(sqls.DB(), cnd)
}

func (s *conversationEventLogService) FindOne(cnd *sqls.Cnd) *models.ConversationEventLog {
	return repositories.ConversationEventLogRepository.FindOne(sqls.DB(), cnd)
}

func (s *conversationEventLogService) FindPageByParams(params *params.QueryParams) (list []models.ConversationEventLog, paging *sqls.Paging) {
	return repositories.ConversationEventLogRepository.FindPageByParams(sqls.DB(), params)
}

func (s *conversationEventLogService) FindPageByCnd(cnd *sqls.Cnd) (list []models.ConversationEventLog, paging *sqls.Paging) {
	return repositories.ConversationEventLogRepository.FindPageByCnd(sqls.DB(), cnd)
}

func (s *conversationEventLogService) Count(cnd *sqls.Cnd) int64 {
	return repositories.ConversationEventLogRepository.Count(sqls.DB(), cnd)
}

func (s *conversationEventLogService) Create(t *models.ConversationEventLog) error {
	return repositories.ConversationEventLogRepository.Create(sqls.DB(), t)
}

func (s *conversationEventLogService) Update(t *models.ConversationEventLog) error {
	return repositories.ConversationEventLogRepository.Update(sqls.DB(), t)
}

func (s *conversationEventLogService) Updates(id int64, columns map[string]interface{}) error {
	return repositories.ConversationEventLogRepository.Updates(sqls.DB(), id, columns)
}

func (s *conversationEventLogService) UpdateColumn(id int64, name string, value interface{}) error {
	return repositories.ConversationEventLogRepository.UpdateColumn(sqls.DB(), id, name, value)
}

func (s *conversationEventLogService) Delete(id int64) {
	repositories.ConversationEventLogRepository.Delete(sqls.DB(), id)
}

func (s *conversationEventLogService) CreateEvent(ctx *sqls.TxContext, conversationID int64, eventType enums.IMEventType, operatorType enums.IMSenderType, operatorID int64, content, payload string) error {
	return s.CreateEventWithRequestID(ctx, conversationID, "", eventType, operatorType, operatorID, content, payload)
}

func (s *conversationEventLogService) CreateEventWithRequestID(ctx *sqls.TxContext, conversationID int64, requestID string, eventType enums.IMEventType, operatorType enums.IMSenderType, operatorID int64, content, payload string) error {
	return repositories.ConversationEventLogRepository.Create(ctx.Tx, &models.ConversationEventLog{
		ConversationID: conversationID,
		RequestID:      tracex.NormalizeRequestID(requestID),
		EventType:      eventType,
		OperatorType:   operatorType,
		OperatorID:     operatorID,
		Content:        strings.TrimSpace(content),
		Payload:        strings.TrimSpace(payload),
		CreatedAt:      time.Now(),
	})
}
