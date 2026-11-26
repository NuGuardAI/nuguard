package ai

import (
	"time"

	"github.com/mlogclub/simple/sqls"
	openai "github.com/openai/openai-go/v3"
	"github.com/openai/openai-go/v3/option"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/repositories"
)

func newOpenAIClient(config models.AIConfig) openai.Client {
	opts := []option.RequestOption{
		option.WithAPIKey(config.APIKey),
		option.WithBaseURL(config.BaseURL),
	}
	if config.TimeoutMS > 0 {
		opts = append(opts, option.WithRequestTimeout(time.Duration(config.TimeoutMS)*time.Millisecond))
	}
	if config.MaxRetryCount >= 0 {
		opts = append(opts, option.WithMaxRetries(config.MaxRetryCount))
	}

	return openai.NewClient(opts...)
}

func GetEnabledAIConfig(modelType enums.AIModelType) (*models.AIConfig, error) {
	item := repositories.AIConfigRepository.GetEnabled(sqls.DB(), modelType)
	if item == nil {
		return nil, errorsx.BusinessErrorI18n(2005, "error.aiConfig.noneEnabled")
	}
	return item, nil
}
