package dashboard

import (
	"testing"

	"agent-desk/internal/models"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

func TestBuildAIAgentResponseExposesPublishedRevision(t *testing.T) {
	setupAIAgentHandlerTestDB(t)

	published := buildAIAgentResponse(&models.AIAgent{PublishedRevisionID: 12})
	if published.PublishedRevisionID != 12 {
		t.Fatalf("published revision = %d, want 12", published.PublishedRevisionID)
	}

	rollout := buildAIAgentResponse(&models.AIAgent{RolloutPercent: 20, PreviousRolloutPercent: 100})
	if rollout.RolloutPercent != 20 || rollout.PreviousRolloutPercent != 100 {
		t.Fatalf("unexpected rollout response: %#v", rollout)
	}
}

func setupAIAgentHandlerTestDB(t *testing.T) {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite db: %v", err)
	}
	sqlDB, err := db.DB()
	if err != nil {
		t.Fatalf("get sqlite db: %v", err)
	}
	t.Cleanup(func() {
		if err := sqlDB.Close(); err != nil {
			t.Fatalf("close sqlite db: %v", err)
		}
	})
	if err := db.AutoMigrate(
		&models.AIConfig{},
		&models.AgentTeam{},
		&models.KnowledgeBase{},
		&models.SkillDefinition{},
	); err != nil {
		t.Fatalf("auto migrate: %v", err)
	}
	sqls.SetDB(db)
}
