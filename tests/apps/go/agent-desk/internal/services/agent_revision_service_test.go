package services

import (
	"encoding/json"
	"strings"
	"testing"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

func TestAgentRevisionServiceRestoresPublishedSnapshotAndKeepsAPIKey(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+strings.ReplaceAll(t.Name(), "/", "_")+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if err := db.AutoMigrate(&models.AgentRevision{}, &models.AIConfig{}); err != nil {
		t.Fatalf("auto migrate: %v", err)
	}
	sqls.SetDB(db)
	definition := agentRevisionDefinition{
		Agent: agentRevisionAgent{
			Name: "published agent", AIConfigID: 8,
			MaxSteps: 5, ContextWindow: 9, SystemPrompt: "published instruction", KnowledgeIDs: "4", ReplyTimeoutSeconds: 90,
		},
		Model: agentRevisionModel{ConfigID: 8, Provider: string(enums.AIProviderOpenAI), BaseURL: "https://published.example/v1", ModelType: string(enums.AIModelTypeLLM), ModelName: "published-model", TimeoutMS: 12000},
	}
	data, err := json.Marshal(definition)
	if err != nil {
		t.Fatalf("marshal definition: %v", err)
	}
	revision := &models.AgentRevision{AgentID: 7, Revision: 1, Status: enums.StatusOk, Definition: string(data)}
	if err := db.Create(revision).Error; err != nil {
		t.Fatalf("create revision: %v", err)
	}
	snapshot, err := AgentRevisionService.ResolvePublishedSnapshot(models.AIAgent{ID: 7, PublishedRevisionID: revision.ID, SystemPrompt: "draft instruction"}, models.AIConfig{ID: 8, APIKey: "rotated-secret", ModelName: "draft-model"})
	if err != nil {
		t.Fatalf("ResolvePublishedSnapshot: %v", err)
	}
	if snapshot.Agent.SystemPrompt != "published instruction" || snapshot.Agent.MaxSteps != 5 || snapshot.Agent.ReplyTimeoutSeconds != 90 {
		t.Fatalf("agent snapshot not restored: %#v", snapshot.Agent)
	}
	if snapshot.AIConfig.ModelName != "published-model" || snapshot.AIConfig.BaseURL != "https://published.example/v1" || snapshot.AIConfig.APIKey != "rotated-secret" {
		t.Fatalf("model snapshot not restored safely: %#v", snapshot.AIConfig)
	}
}

func TestAgentRevisionServiceUsesPublishedModelConfigAfterDraftConfigChanges(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+strings.ReplaceAll(t.Name(), "/", "_")+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if err := db.AutoMigrate(&models.AgentRevision{}, &models.AIConfig{}); err != nil {
		t.Fatalf("auto migrate: %v", err)
	}
	sqls.SetDB(db)

	publishedConfig := &models.AIConfig{
		Name: "published", Status: enums.StatusOk, Provider: enums.AIProviderOpenAI,
		ModelType: enums.AIModelTypeLLM, ModelName: "current-published-model", APIKey: "published-secret",
	}
	draftConfig := &models.AIConfig{
		Name: "draft", Status: enums.StatusOk, Provider: enums.AIProviderOpenAI,
		ModelType: enums.AIModelTypeLLM, ModelName: "draft-model", APIKey: "draft-secret",
	}
	if err := db.Create(publishedConfig).Error; err != nil {
		t.Fatalf("create published config: %v", err)
	}
	if err := db.Create(draftConfig).Error; err != nil {
		t.Fatalf("create draft config: %v", err)
	}

	definition := agentRevisionDefinition{
		Agent: agentRevisionAgent{AIConfigID: publishedConfig.ID, SystemPrompt: "published instruction"},
		Model: agentRevisionModel{
			ConfigID: publishedConfig.ID, Provider: string(enums.AIProviderOpenAI),
			ModelType: string(enums.AIModelTypeLLM), ModelName: "snapshotted-published-model",
		},
	}
	data, err := json.Marshal(definition)
	if err != nil {
		t.Fatalf("marshal definition: %v", err)
	}
	revision := &models.AgentRevision{AgentID: 7, Revision: 1, Status: enums.StatusOk, Definition: string(data)}
	if err := db.Create(revision).Error; err != nil {
		t.Fatalf("create revision: %v", err)
	}

	snapshot, err := AgentRevisionService.ResolvePublishedSnapshot(
		models.AIAgent{ID: 7, AIConfigID: draftConfig.ID, PublishedRevisionID: revision.ID},
		*draftConfig,
	)
	if err != nil {
		t.Fatalf("ResolvePublishedSnapshot: %v", err)
	}
	if snapshot.AIConfig.ID != publishedConfig.ID {
		t.Fatalf("model config id = %d, want published config %d", snapshot.AIConfig.ID, publishedConfig.ID)
	}
	if snapshot.AIConfig.ModelName != "snapshotted-published-model" || snapshot.AIConfig.APIKey != "published-secret" {
		t.Fatalf("published model snapshot not restored safely: %#v", snapshot.AIConfig)
	}
}
