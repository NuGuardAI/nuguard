package services

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"agent-desk/internal/ai/rag"
	"agent-desk/internal/ai/workflow/dsl"
	workflowregistry "agent-desk/internal/ai/workflow/registry"
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

var KnowledgeBaseService = newKnowledgeBaseService()

func newKnowledgeBaseService() *knowledgeBaseService {
	return &knowledgeBaseService{}
}

type knowledgeBaseService struct {
}

func (s *knowledgeBaseService) Get(id int64) *models.KnowledgeBase {
	return repositories.KnowledgeBaseRepository.Get(sqls.DB(), id)
}

func (s *knowledgeBaseService) Take(where ...interface{}) *models.KnowledgeBase {
	return repositories.KnowledgeBaseRepository.Take(sqls.DB(), where...)
}

func (s *knowledgeBaseService) Find(cnd *sqls.Cnd) []models.KnowledgeBase {
	return repositories.KnowledgeBaseRepository.Find(sqls.DB(), cnd)
}

func (s *knowledgeBaseService) FindOne(cnd *sqls.Cnd) *models.KnowledgeBase {
	return repositories.KnowledgeBaseRepository.FindOne(sqls.DB(), cnd)
}

func (s *knowledgeBaseService) FindPageByParams(params *params.QueryParams) (list []models.KnowledgeBase, paging *sqls.Paging) {
	return repositories.KnowledgeBaseRepository.FindPageByParams(sqls.DB(), params)
}

func (s *knowledgeBaseService) FindPageByCnd(cnd *sqls.Cnd) (list []models.KnowledgeBase, paging *sqls.Paging) {
	return repositories.KnowledgeBaseRepository.FindPageByCnd(sqls.DB(), cnd)
}

func (s *knowledgeBaseService) Count(cnd *sqls.Cnd) int64 {
	return repositories.KnowledgeBaseRepository.Count(sqls.DB(), cnd)
}

func (s *knowledgeBaseService) Create(t *models.KnowledgeBase) error {
	return repositories.KnowledgeBaseRepository.Create(sqls.DB(), t)
}

func (s *knowledgeBaseService) Update(t *models.KnowledgeBase) error {
	return repositories.KnowledgeBaseRepository.Update(sqls.DB(), t)
}

func (s *knowledgeBaseService) Updates(id int64, columns map[string]interface{}) error {
	return repositories.KnowledgeBaseRepository.Updates(sqls.DB(), id, columns)
}

func (s *knowledgeBaseService) UpdateColumn(id int64, name string, value interface{}) error {
	return repositories.KnowledgeBaseRepository.UpdateColumn(sqls.DB(), id, name, value)
}

func (s *knowledgeBaseService) Delete(id int64) {
	repositories.KnowledgeBaseRepository.Delete(sqls.DB(), id)
}

func (s *knowledgeBaseService) CreateKnowledgeBase(req request.CreateKnowledgeBaseRequest, operator *dto.AuthPrincipal) (*models.KnowledgeBase, error) {
	if operator == nil {
		return nil, errorsx.UnauthorizedI18n("error.auth.expired")
	}
	item, err := s.buildKnowledgeBaseModel(req)
	if err != nil {
		return nil, err
	}
	item.Status = enums.StatusOk
	item.AuditFields = utils.BuildAuditFields(operator)
	if err := repositories.KnowledgeBaseRepository.Create(sqls.DB(), item); err != nil {
		return nil, err
	}
	return item, nil
}

func (s *knowledgeBaseService) UpdateKnowledgeBase(req request.UpdateKnowledgeBaseRequest, operator *dto.AuthPrincipal) error {
	if operator == nil {
		return errorsx.UnauthorizedI18n("error.auth.expired")
	}
	current := s.Get(req.ID)
	if current == nil {
		return errorsx.InvalidParamI18n("error.e0283")
	}
	item, err := s.buildKnowledgeBaseModel(req.CreateKnowledgeBaseRequest)
	if err != nil {
		return err
	}
	return repositories.KnowledgeBaseRepository.Updates(sqls.DB(), req.ID, map[string]any{
		"name":                    item.Name,
		"description":             item.Description,
		"knowledge_type":          item.KnowledgeType,
		"default_top_k":           item.DefaultTopK,
		"default_score_threshold": item.DefaultScoreThreshold,
		"default_rerank_limit":    item.DefaultRerankLimit,
		"chunk_provider":          item.ChunkProvider,
		"chunk_target_tokens":     item.ChunkTargetTokens,
		"chunk_max_tokens":        item.ChunkMaxTokens,
		"chunk_overlap_tokens":    item.ChunkOverlapTokens,
		"answer_mode":             item.AnswerMode,
		"remark":                  item.Remark,
		"update_user_id":          operator.UserID,
		"update_user_name":        operator.Username,
		"updated_at":              time.Now(),
	})
}

func (s *knowledgeBaseService) DeleteKnowledgeBase(id int64) error {
	current := s.Get(id)
	if current == nil {
		return errorsx.InvalidParamI18n("error.e0283")
	}

	referencingWorkflows := s.findWorkflowReferencesByKnowledgeBaseID(id)
	if len(referencingWorkflows) > 0 {
		if len(referencingWorkflows) == 1 {
			return errorsx.Forbidden(fmt.Sprintf("知识库正在被流程「%s」使用，请先从知识检索节点中移除", referencingWorkflows[0]))
		}
		return errorsx.Forbidden(fmt.Sprintf("知识库正在被 %d 个流程使用，请先从知识检索节点中移除", len(referencingWorkflows)))
	}

	if err := sqls.WithTransaction(func(ctx *sqls.TxContext) error {
		if err := repositories.KnowledgeDocumentRepository.DeleteByKnowledgeBaseID(ctx.Tx, id); err != nil {
			return err
		}
		if err := repositories.KnowledgeFAQRepository.DeleteByKnowledgeBaseID(ctx.Tx, id); err != nil {
			return err
		}
		return ctx.Tx.Delete(&models.KnowledgeBase{}, "id = ?", id).Error
	}); err != nil {
		return err
	}

	return rag.Index.RemoveKnowledgeBaseIndex(context.Background(), id)
}

func (s *knowledgeBaseService) findWorkflowReferencesByKnowledgeBaseID(id int64) []string {
	names := make(map[string]struct{})
	workflows := repositories.AIWorkflowRepository.Find(sqls.DB(), sqls.NewCnd().Eq("status", enums.StatusOk))
	workflowNames := make(map[int64]string, len(workflows))
	for _, workflow := range workflows {
		name := strings.TrimSpace(workflow.Name)
		if name == "" {
			name = fmt.Sprintf("ID %d", workflow.ID)
		}
		workflowNames[workflow.ID] = name
		if workflowDefinitionUsesKnowledgeBase(workflow.DraftDefinition, id) {
			names[name] = struct{}{}
		}
	}
	versions := repositories.AIWorkflowVersionRepository.Find(sqls.DB(), sqls.NewCnd())
	for _, version := range versions {
		if !workflowDefinitionUsesKnowledgeBase(version.Definition, id) {
			continue
		}
		name := workflowNames[version.WorkflowID]
		if strings.TrimSpace(name) == "" {
			name = fmt.Sprintf("ID %d", version.WorkflowID)
		}
		names[name] = struct{}{}
	}
	ret := make([]string, 0, len(names))
	for name := range names {
		ret = append(ret, name)
	}
	return ret
}

func workflowDefinitionUsesKnowledgeBase(definition string, id int64) bool {
	definition = strings.TrimSpace(definition)
	if definition == "" {
		return false
	}
	var def dsl.Definition
	if err := json.Unmarshal([]byte(definition), &def); err != nil {
		return false
	}
	for _, node := range def.Nodes {
		if strings.TrimSpace(node.Type) != workflowregistry.NodeTypeKnowledgeRetrieve {
			continue
		}
		for _, knowledgeBaseID := range knowledgeBaseIDsFromWorkflowNodeConfig(node.Data.Config) {
			if knowledgeBaseID == id {
				return true
			}
		}
	}
	return false
}

func knowledgeBaseIDsFromWorkflowNodeConfig(raw json.RawMessage) []int64 {
	if len(raw) == 0 {
		return nil
	}
	var cfg map[string]any
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return nil
	}
	items, ok := cfg["knowledgeBaseIds"].([]any)
	if !ok {
		return nil
	}
	ret := make([]int64, 0, len(items))
	for _, item := range items {
		switch value := item.(type) {
		case float64:
			ret = append(ret, int64(value))
		case int64:
			ret = append(ret, value)
		case int:
			ret = append(ret, int64(value))
		}
	}
	return ret
}

func (s *knowledgeBaseService) UpdateSort(ids []int64) error {
	return sqls.WithTransaction(func(ctx *sqls.TxContext) error {
		for i, id := range ids {
			if err := repositories.KnowledgeBaseRepository.UpdateColumn(ctx.Tx, id, "sort_no", i); err != nil {
				return err
			}
		}
		return nil
	})
}

func (s *knowledgeBaseService) buildKnowledgeBaseModel(req request.CreateKnowledgeBaseRequest) (*models.KnowledgeBase, error) {
	item := &models.KnowledgeBase{
		Name:                  req.Name,
		Description:           req.Description,
		KnowledgeType:         req.KnowledgeType,
		DefaultTopK:           req.DefaultTopK,
		DefaultScoreThreshold: req.DefaultScoreThreshold,
		DefaultRerankLimit:    req.DefaultRerankLimit,
		ChunkProvider:         req.ChunkProvider,
		ChunkTargetTokens:     req.ChunkTargetTokens,
		ChunkMaxTokens:        req.ChunkMaxTokens,
		ChunkOverlapTokens:    req.ChunkOverlapTokens,
		AnswerMode:            req.AnswerMode,
		Remark:                req.Remark,
	}
	if item.DefaultTopK == 0 {
		item.DefaultTopK = 10
	}
	if item.KnowledgeType == "" {
		item.KnowledgeType = string(enums.KnowledgeBaseTypeDocument)
	}
	if !isValidKnowledgeType(item.KnowledgeType) {
		return nil, errorsx.InvalidParamI18n("error.e0290")
	}
	if item.DefaultScoreThreshold == 0 {
		item.DefaultScoreThreshold = 0.2
	}
	if item.DefaultRerankLimit == 0 {
		item.DefaultRerankLimit = 5
	}
	if item.ChunkProvider == "" {
		item.ChunkProvider = string(enums.KnowledgeChunkProviderStructured)
	}
	if item.KnowledgeType == string(enums.KnowledgeBaseTypeFAQ) {
		item.ChunkProvider = string(enums.KnowledgeChunkProviderFAQ)
		item.ChunkTargetTokens = 0
		item.ChunkMaxTokens = 0
		item.ChunkOverlapTokens = 0
	} else if item.ChunkProvider == string(enums.KnowledgeChunkProviderFAQ) {
		return nil, errorsx.InvalidParamI18n("error.e0219")
	}
	if !isValidChunkProvider(item.ChunkProvider) {
		return nil, errorsx.InvalidParamI18n("error.e0130")
	}
	if item.KnowledgeType != string(enums.KnowledgeBaseTypeFAQ) && item.ChunkTargetTokens == 0 {
		item.ChunkTargetTokens = 300
	}
	if item.KnowledgeType != string(enums.KnowledgeBaseTypeFAQ) && item.ChunkMaxTokens == 0 {
		item.ChunkMaxTokens = 400
	}
	if item.KnowledgeType != string(enums.KnowledgeBaseTypeFAQ) && item.ChunkMaxTokens < item.ChunkTargetTokens {
		item.ChunkMaxTokens = item.ChunkTargetTokens
	}
	if item.KnowledgeType != string(enums.KnowledgeBaseTypeFAQ) && item.ChunkOverlapTokens == 0 {
		item.ChunkOverlapTokens = 40
	}
	if item.AnswerMode == 0 {
		item.AnswerMode = 1
	}
	return item, nil
}

func isValidChunkProvider(provider string) bool {
	switch provider {
	case string(enums.KnowledgeChunkProviderFixed),
		string(enums.KnowledgeChunkProviderStructured),
		string(enums.KnowledgeChunkProviderFAQ),
		string(enums.KnowledgeChunkProviderSemantic):
		return true
	default:
		return false
	}
}

func isValidKnowledgeType(knowledgeType string) bool {
	switch knowledgeType {
	case string(enums.KnowledgeBaseTypeDocument), string(enums.KnowledgeBaseTypeFAQ):
		return true
	default:
		return false
	}
}
