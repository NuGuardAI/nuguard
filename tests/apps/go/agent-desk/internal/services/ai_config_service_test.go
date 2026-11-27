package services

import (
	"testing"
	"time"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
	"gorm.io/gorm/schema"
)

func TestUpdateAIConfigKeepsAPIKeyWhenRequestAPIKeyBlank(t *testing.T) {
	db := setupAIConfigServiceTestDB(t)
	item := &models.AIConfig{
		Name:        "old",
		Provider:    enums.AIProviderOpenAI,
		BaseURL:     "https://old.example.com",
		APIKey:      "sk-existing",
		ModelType:   enums.AIModelTypeLLM,
		ModelName:   "old-model",
		TimeoutMS:   30000,
		AuditFields: models.AuditFields{CreatedAt: time.Now(), UpdatedAt: time.Now()},
	}
	if err := db.Create(item).Error; err != nil {
		t.Fatalf("create ai config error = %v", err)
	}

	err := AIConfigService.UpdateAIConfig(request.UpdateAIConfigRequest{
		ID: item.ID,
		CreateAIConfigRequest: request.CreateAIConfigRequest{
			Name:      "new",
			Provider:  enums.AIProviderOpenAI,
			BaseURL:   "https://new.example.com",
			APIKey:    "   ",
			ModelType: enums.AIModelTypeLLM,
			ModelName: "new-model",
			TimeoutMS: 120000,
		},
	}, &dto.AuthPrincipal{UserID: 1, Username: "admin"})
	if err != nil {
		t.Fatalf("UpdateAIConfig() error = %v", err)
	}

	var updated models.AIConfig
	if err := db.First(&updated, item.ID).Error; err != nil {
		t.Fatalf("get updated ai config error = %v", err)
	}
	if updated.APIKey != "sk-existing" {
		t.Fatalf("expected api key to be preserved, got %q", updated.APIKey)
	}
}

func setupAIConfigServiceTestDB(t *testing.T) *gorm.DB {
	t.Helper()

	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{
		NamingStrategy: schema.NamingStrategy{
			TablePrefix:   "t_",
			SingularTable: true,
		},
	})
	if err != nil {
		t.Fatalf("open sqlite error = %v", err)
	}
	t.Cleanup(func() {
		sqlDB, err := db.DB()
		if err == nil {
			_ = sqlDB.Close()
		}
	})
	if err := db.AutoMigrate(&models.AIConfig{}); err != nil {
		t.Fatalf("auto migrate error = %v", err)
	}
	sqls.SetDB(db)
	return db
}
