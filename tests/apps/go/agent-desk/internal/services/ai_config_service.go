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

	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/common/strs"
	"github.com/mlogclub/simple/sqls"
)

var AIConfigService = newAIConfigService()

func newAIConfigService() *aIConfigService {
	return &aIConfigService{}
}

type aIConfigService struct {
}

func (s *aIConfigService) Get(id int64) *models.AIConfig {
	return repositories.AIConfigRepository.Get(sqls.DB(), id)
}

func (s *aIConfigService) Take(where ...interface{}) *models.AIConfig {
	return repositories.AIConfigRepository.Take(sqls.DB(), where...)
}

func (s *aIConfigService) Find(cnd *sqls.Cnd) []models.AIConfig {
	return repositories.AIConfigRepository.Find(sqls.DB(), cnd)
}

func (s *aIConfigService) FindOne(cnd *sqls.Cnd) *models.AIConfig {
	return repositories.AIConfigRepository.FindOne(sqls.DB(), cnd)
}

func (s *aIConfigService) FindPageByParams(params *params.QueryParams) (list []models.AIConfig, paging *sqls.Paging) {
	return repositories.AIConfigRepository.FindPageByParams(sqls.DB(), params)
}

func (s *aIConfigService) FindPageByCnd(cnd *sqls.Cnd) (list []models.AIConfig, paging *sqls.Paging) {
	return repositories.AIConfigRepository.FindPageByCnd(sqls.DB(), cnd)
}

func (s *aIConfigService) Count(cnd *sqls.Cnd) int64 {
	return repositories.AIConfigRepository.Count(sqls.DB(), cnd)
}

func (s *aIConfigService) Create(t *models.AIConfig) error {
	return repositories.AIConfigRepository.Create(sqls.DB(), t)
}

func (s *aIConfigService) Update(t *models.AIConfig) error {
	return repositories.AIConfigRepository.Update(sqls.DB(), t)
}

func (s *aIConfigService) Updates(id int64, columns map[string]interface{}) error {
	return repositories.AIConfigRepository.Updates(sqls.DB(), id, columns)
}

func (s *aIConfigService) UpdateColumn(id int64, name string, value interface{}) error {
	return repositories.AIConfigRepository.UpdateColumn(sqls.DB(), id, name, value)
}

func (s *aIConfigService) Delete(id int64) {
	repositories.AIConfigRepository.Delete(sqls.DB(), id)
}

func (s *aIConfigService) CreateAIConfig(req request.CreateAIConfigRequest, operator *dto.AuthPrincipal) (*models.AIConfig, error) {
	if operator == nil {
		return nil, errorsx.UnauthorizedI18n("error.auth.expired")
	}
	item, err := s.buildAIConfigModel(req)
	if err != nil {
		return nil, err
	}

	item.Status = enums.StatusDisabled
	item.SortNo = s.nextSortNo()
	item.AuditFields = utils.BuildAuditFields(operator)
	if err := sqls.WithTransaction(func(ctx *sqls.TxContext) error {
		if err := ctx.Tx.Create(item).Error; err != nil {
			return err
		}
		return nil
	}); err != nil {
		return nil, err
	}
	return item, nil
}

func (s *aIConfigService) UpdateAIConfig(req request.UpdateAIConfigRequest, operator *dto.AuthPrincipal) error {
	if operator == nil {
		return errorsx.UnauthorizedI18n("error.auth.expired")
	}
	current := s.Get(req.ID)
	if current == nil {
		return errorsx.InvalidParamI18n("error.e0012")
	}
	item, err := s.buildAIConfigModel(req.CreateAIConfigRequest)
	if err != nil {
		return err
	}

	columns := map[string]any{
		"name":               item.Name,
		"provider":           item.Provider,
		"base_url":           item.BaseURL,
		"model_type":         item.ModelType,
		"model_name":         item.ModelName,
		"dimension":          item.Dimension,
		"max_context_tokens": item.MaxContextTokens,
		"max_output_tokens":  item.MaxOutputTokens,
		"timeout_ms":         item.TimeoutMS,
		"max_retry_count":    item.MaxRetryCount,
		"rpm_limit":          item.RPMLimit,
		"tpm_limit":          item.TPMLimit,
		"remark":             item.Remark,
		"update_user_id":     operator.UserID,
		"update_user_name":   operator.Username,
		"updated_at":         time.Now(),
	}
	if item.APIKey != "" {
		columns["api_key"] = item.APIKey
	}
	return repositories.AIConfigRepository.Updates(sqls.DB(), req.ID, columns)
}

func (s *aIConfigService) DeleteAIConfig(id int64, operator *dto.AuthPrincipal) error {
	current := s.Get(id)
	if current == nil {
		return nil
	}
	if current.Status == enums.StatusOk {
		return errorsx.ForbiddenI18n("error.e0143")
	}
	return repositories.AIConfigRepository.Updates(sqls.DB(), id, map[string]any{
		"status":           enums.StatusDeleted,
		"update_user_id":   operator.UserID,
		"update_user_name": operator.Username,
		"updated_at":       time.Now(),
	})
}

func (s *aIConfigService) UpdateStatus(id int64, status enums.Status, operator *dto.AuthPrincipal) error {
	if operator == nil {
		return errorsx.UnauthorizedI18n("error.auth.expired")
	}
	current := s.Get(id)
	if current == nil {
		return errorsx.InvalidParamI18n("error.e0012")
	}
	if status != enums.StatusOk && status != enums.StatusDisabled {
		return errorsx.InvalidParamI18n("error.e0254")
	}

	return sqls.WithTransaction(func(ctx *sqls.TxContext) error {
		if status == enums.StatusOk {
			if err := s.disableOthersByModelType(ctx, current.ModelType, id); err != nil {
				return err
			}
		}
		return repositories.AIConfigRepository.Updates(ctx.Tx, id, map[string]any{
			"status":           status,
			"update_user_id":   operator.UserID,
			"update_user_name": operator.Username,
			"updated_at":       time.Now(),
		})
	})
}

func (s *aIConfigService) disableOthersByModelType(ctx *sqls.TxContext, modelType enums.AIModelType, excludeID int64) error {
	query := ctx.Tx.Model(&models.AIConfig{}).Where("model_type = ?", modelType)
	if excludeID > 0 {
		query = query.Where("id <> ?", excludeID)
	}
	return query.Updates(map[string]any{
		"status":     int(enums.StatusDisabled),
		"updated_at": time.Now(),
	}).Error
}

func (s *aIConfigService) UpdateSort(ids []int64) error {
	return sqls.WithTransaction(func(ctx *sqls.TxContext) error {
		for i, id := range ids {
			if err := repositories.AIConfigRepository.UpdateColumn(ctx.Tx, id, "sort_no", i); err != nil {
				return err
			}
		}
		return nil
	})
}

func (s *aIConfigService) buildAIConfigModel(req request.CreateAIConfigRequest) (*models.AIConfig, error) {
	name := strings.TrimSpace(req.Name)
	baseURL := strings.TrimSpace(req.BaseURL)
	modelName := strings.TrimSpace(req.ModelName)

	if name == "" {
		return nil, errorsx.InvalidParamI18n("error.e0339")
	}
	if strs.IsBlank(string(req.Provider)) {
		return nil, errorsx.InvalidParamI18n("error.e0122")
	}
	if baseURL == "" {
		return nil, errorsx.InvalidParamI18n("error.e0147")
	}
	if strs.IsBlank(string(req.ModelType)) {
		return nil, errorsx.InvalidParamI18n("error.e0243")
	}
	if modelName == "" {
		return nil, errorsx.InvalidParamI18n("error.e0242")
	}
	if req.Dimension < 0 {
		req.Dimension = 0
	}
	if req.MaxContextTokens < 0 {
		req.MaxContextTokens = 0
	}
	if req.MaxOutputTokens < 0 {
		req.MaxOutputTokens = 0
	}
	if req.TimeoutMS <= 0 {
		req.TimeoutMS = 30000
	}
	if req.MaxRetryCount < 0 {
		req.MaxRetryCount = 0
	}
	if req.RPMLimit < 0 {
		req.RPMLimit = 0
	}
	if req.TPMLimit < 0 {
		req.TPMLimit = 0
	}

	return &models.AIConfig{
		Name:             name,
		Provider:         req.Provider,
		BaseURL:          baseURL,
		APIKey:           strings.TrimSpace(req.APIKey),
		ModelType:        req.ModelType,
		ModelName:        modelName,
		Dimension:        req.Dimension,
		MaxContextTokens: req.MaxContextTokens,
		MaxOutputTokens:  req.MaxOutputTokens,
		TimeoutMS:        req.TimeoutMS,
		MaxRetryCount:    req.MaxRetryCount,
		RPMLimit:         req.RPMLimit,
		TPMLimit:         req.TPMLimit,
		Remark:           strings.TrimSpace(req.Remark),
	}, nil
}

func (s *aIConfigService) nextSortNo() int {
	if latest := s.FindOne(sqls.NewCnd().Desc("sort_no").Desc("id")); latest != nil {
		return latest.SortNo + 1
	}
	return 1
}
