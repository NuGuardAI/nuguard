package services

import (
	"strings"
	"testing"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
	"gorm.io/gorm/schema"
)

func TestUpdateAIAgentKeepsPublishedRevisionActive(t *testing.T) {
	dbName := strings.NewReplacer("/", "_", " ", "_").Replace(t.Name())
	db, err := gorm.Open(sqlite.Open("file:"+dbName+"?mode=memory&cache=shared"), &gorm.Config{
		NamingStrategy: schema.NamingStrategy{
			TablePrefix:   "t_",
			SingularTable: true,
		},
	})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	t.Cleanup(func() {
		sqlDB, dbErr := db.DB()
		if dbErr == nil {
			_ = sqlDB.Close()
		}
	})
	if err := db.AutoMigrate(&models.AIConfig{}, &models.AIAgent{}, &models.AIAgentWorkflowBinding{}); err != nil {
		t.Fatalf("auto migrate: %v", err)
	}
	sqls.SetDB(db)

	config := &models.AIConfig{
		Name: "test", Status: enums.StatusOk, Provider: enums.AIProviderOpenAI,
		ModelType: enums.AIModelTypeLLM, ModelName: "test-model",
	}
	if err := db.Create(config).Error; err != nil {
		t.Fatalf("create ai config: %v", err)
	}
	agent := &models.AIAgent{
		Name: "published agent", Status: enums.StatusOk, AIConfigID: config.ID,
		ServiceMode: enums.IMConversationServiceModeAIFirst, HandoffMode: enums.AIAgentHandoffModeWaitPool,
		FallbackMode: enums.AIAgentFallbackModeNoAnswer, RolloutPercent: 100, PublishedRevisionID: 18,
	}
	if err := db.Create(agent).Error; err != nil {
		t.Fatalf("create ai agent: %v", err)
	}

	err = AIAgentService.UpdateAIAgent(request.UpdateAIAgentRequest{
		ID: agent.ID,
		CreateAIAgentRequest: request.CreateAIAgentRequest{
			Name: "updated draft", AIConfigID: config.ID,
			ServiceMode:    enums.IMConversationServiceModeAIFirst,
			HandoffMode:    enums.AIAgentHandoffModeWaitPool,
			FallbackMode:   enums.AIAgentFallbackModeNoAnswer,
			RolloutPercent: 100,
		},
	}, &dto.AuthPrincipal{UserID: 1, Username: "admin"})
	if err != nil {
		t.Fatalf("UpdateAIAgent: %v", err)
	}

	var updated models.AIAgent
	if err := db.First(&updated, agent.ID).Error; err != nil {
		t.Fatalf("get updated ai agent: %v", err)
	}
	if updated.PublishedRevisionID != agent.PublishedRevisionID {
		t.Fatalf("published revision id = %d, want %d", updated.PublishedRevisionID, agent.PublishedRevisionID)
	}
	if updated.Name != "updated draft" {
		t.Fatalf("draft name = %q, want updated draft", updated.Name)
	}
}
