package agent

import (
	"context"
	"strings"
	"time"

	modelopenai "github.com/cloudwego/eino-ext/components/model/openai"
	"github.com/cloudwego/eino/components/model"
)

type OpenAIModelConfig struct {
	BaseURL string
	APIKey  string
	Model   string
	Timeout time.Duration
}

func NewOpenAICompatibleModel(ctx context.Context, config OpenAIModelConfig) (model.BaseChatModel, error) {
	config.BaseURL = strings.TrimSpace(config.BaseURL)
	config.APIKey = strings.TrimSpace(config.APIKey)
	config.Model = strings.TrimSpace(config.Model)
	if config.BaseURL == "" || config.APIKey == "" || config.Model == "" {
		return nil, ErrInvalidModelConfig
	}
	if config.Timeout <= 0 {
		config.Timeout = 30 * time.Second
	}
	chatModel, err := modelopenai.NewChatModel(ctx, &modelopenai.ChatModelConfig{
		BaseURL: config.BaseURL,
		APIKey:  config.APIKey,
		Model:   config.Model,
		Timeout: config.Timeout,
	})
	if err != nil {
		return nil, err
	}
	return chatModel, nil
}
