package services

import (
	"encoding/json"
	"strings"
	"time"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/toolx"
	"agent-desk/internal/pkg/utils"
	"agent-desk/internal/repositories"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
)

var SkillDefinitionService = newSkillDefinitionService()

func newSkillDefinitionService() *skillDefinitionService {
	return &skillDefinitionService{}
}

type skillDefinitionService struct {
}

func (s *skillDefinitionService) Get(id int64) *models.SkillDefinition {
	return repositories.SkillDefinitionRepository.Get(sqls.DB(), id)
}

func (s *skillDefinitionService) Take(where ...interface{}) *models.SkillDefinition {
	return repositories.SkillDefinitionRepository.Take(sqls.DB(), where...)
}

func (s *skillDefinitionService) Find(cnd *sqls.Cnd) []models.SkillDefinition {
	return repositories.SkillDefinitionRepository.Find(sqls.DB(), cnd)
}

func (s *skillDefinitionService) FindOne(cnd *sqls.Cnd) *models.SkillDefinition {
	return repositories.SkillDefinitionRepository.FindOne(sqls.DB(), cnd)
}

func (s *skillDefinitionService) FindPageByParams(params *params.QueryParams) (list []models.SkillDefinition, paging *sqls.Paging) {
	return repositories.SkillDefinitionRepository.FindPageByParams(sqls.DB(), params)
}

func (s *skillDefinitionService) FindPageByCnd(cnd *sqls.Cnd) (list []models.SkillDefinition, paging *sqls.Paging) {
	return repositories.SkillDefinitionRepository.FindPageByCnd(sqls.DB(), cnd)
}

func (s *skillDefinitionService) Count(cnd *sqls.Cnd) int64 {
	return repositories.SkillDefinitionRepository.Count(sqls.DB(), cnd)
}

func (s *skillDefinitionService) Create(t *models.SkillDefinition) error {
	return repositories.SkillDefinitionRepository.Create(sqls.DB(), t)
}

func (s *skillDefinitionService) Update(t *models.SkillDefinition) error {
	return repositories.SkillDefinitionRepository.Update(sqls.DB(), t)
}

func (s *skillDefinitionService) Updates(id int64, columns map[string]interface{}) error {
	return repositories.SkillDefinitionRepository.Updates(sqls.DB(), id, columns)
}

func (s *skillDefinitionService) UpdateColumn(id int64, name string, value interface{}) error {
	return repositories.SkillDefinitionRepository.UpdateColumn(sqls.DB(), id, name, value)
}

func (s *skillDefinitionService) Delete(id int64) {
	repositories.SkillDefinitionRepository.Delete(sqls.DB(), id)
}

func (s *skillDefinitionService) GetByIDs(ids []int64) map[int64]models.SkillDefinition {
	return repositories.SkillDefinitionRepository.GetByIDs(sqls.DB(), ids)
}

func (s *skillDefinitionService) CreateSkillDefinition(req request.CreateSkillDefinitionRequest, operator *dto.AuthPrincipal) (*models.SkillDefinition, error) {
	if operator == nil {
		return nil, errorsx.UnauthorizedI18n("error.auth.expired")
	}
	normalized, err := s.normalizeSkillDefinitionRequest(req)
	if err != nil {
		return nil, err
	}
	item := &models.SkillDefinition{
		Name:          normalized.Name,
		Description:   normalized.Description,
		Instruction:   normalized.Instruction,
		Examples:      mustMarshalSkillStringArray(normalized.Examples),
		ToolWhitelist: mustMarshalSkillStringArray(normalized.ToolWhitelist),
		Status:        enums.StatusOk,
		Remark:        normalized.Remark,
		AuditFields:   utils.BuildAuditFields(operator),
	}
	if err := repositories.SkillDefinitionRepository.Create(sqls.DB(), item); err != nil {
		return nil, err
	}
	return item, nil
}

func (s *skillDefinitionService) UpdateSkillDefinition(req request.UpdateSkillDefinitionRequest, operator *dto.AuthPrincipal) error {
	if operator == nil {
		return errorsx.UnauthorizedI18n("error.auth.expired")
	}
	if req.ID <= 0 {
		return errorsx.InvalidParamI18n("error.e0052")
	}
	current := s.Get(req.ID)
	if current == nil {
		return errorsx.InvalidParamI18n("error.e0053")
	}
	normalized, err := s.normalizeSkillDefinitionRequest(req.CreateSkillDefinitionRequest)
	if err != nil {
		return err
	}
	return repositories.SkillDefinitionRepository.Updates(sqls.DB(), req.ID, map[string]any{
		"name":             normalized.Name,
		"description":      normalized.Description,
		"instruction":      normalized.Instruction,
		"examples":         mustMarshalSkillStringArray(normalized.Examples),
		"tool_whitelist":   mustMarshalSkillStringArray(normalized.ToolWhitelist),
		"remark":           normalized.Remark,
		"update_user_id":   operator.UserID,
		"update_user_name": operator.Username,
		"updated_at":       time.Now(),
	})
}

func (s *skillDefinitionService) normalizeSkillDefinitionRequest(req request.CreateSkillDefinitionRequest) (*request.CreateSkillDefinitionRequest, error) {
	normalized := &request.CreateSkillDefinitionRequest{
		Name:        strings.TrimSpace(req.Name),
		Description: strings.TrimSpace(req.Description),
		Instruction: strings.TrimSpace(req.Instruction),
		Remark:      strings.TrimSpace(req.Remark),
	}
	if normalized.Name == "" {
		return nil, errorsx.InvalidParamI18n("error.e0055")
	}
	if normalized.Instruction == "" {
		return nil, errorsx.InvalidParamI18n("error.e0207")
	}
	examples, err := normalizeSkillStringArray(req.Examples)
	if err != nil {
		return nil, err
	}
	toolWhitelist, err := normalizeSkillStringArray(req.ToolWhitelist)
	if err != nil {
		return nil, err
	}
	for _, toolCode := range toolWhitelist {
		if err := ToolCatalogService.ValidateMCPToolCode(toolCode); err != nil {
			return nil, err
		}
	}
	normalized.Examples = examples
	normalized.ToolWhitelist = toolWhitelist
	return normalized, nil
}

func normalizeSkillStringArray(input []string) ([]string, error) {
	buf, err := json.Marshal(input)
	if err != nil {
		return nil, errorsx.InvalidParamI18n("error.e0031")
	}
	var ret []string
	if err := json.Unmarshal(buf, &ret); err != nil {
		return nil, errorsx.InvalidParamI18n("error.e0031")
	}
	normalized := make([]string, 0, len(ret))
	seen := make(map[string]struct{}, len(ret))
	for _, item := range ret {
		item = strings.TrimSpace(item)
		item = toolx.NormalizeToolCodeAlias(item)
		if item == "" {
			continue
		}
		if _, ok := seen[item]; ok {
			continue
		}
		seen[item] = struct{}{}
		normalized = append(normalized, item)
	}
	return normalized, nil
}

func mustMarshalSkillStringArray(input []string) string {
	items, err := normalizeSkillStringArray(input)
	if err != nil || len(items) == 0 {
		return "[]"
	}
	buf, err := json.Marshal(items)
	if err != nil {
		return "[]"
	}
	return string(buf)
}
