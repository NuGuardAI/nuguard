package runtime

import (
	"strings"
	"testing"
	"time"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
	"gorm.io/gorm/schema"
)

func TestReplyCommitStoresWorkflowRunIDOnAIMessage(t *testing.T) {
	db := setupReplyCommitTestDB(t)
	aiAgent := createReplyCommitTestAIAgent(t, db)
	conversation := createReplyCommitTestConversation(t, db, aiAgent.ID)

	replyMessage, err := newReplyCommitService().CommitAIReply(replyCommitInput{
		Conversation:  *conversation,
		Message:       models.Message{ID: 101, RequestID: "trace-101"},
		AIAgent:       *aiAgent,
		ReplyText:     "AI reply",
		ClientPrefix:  "ai_reply",
		WorkflowRunID: 9988,
	})
	if err != nil {
		t.Fatalf("CommitAIReply() error = %v", err)
	}
	if replyMessage == nil {
		t.Fatalf("expected reply message")
	}
	if replyMessage.WorkflowRunID != 9988 {
		t.Fatalf("replyMessage.WorkflowRunID=%d want 9988", replyMessage.WorkflowRunID)
	}

	var stored models.Message
	if err := db.First(&stored, replyMessage.ID).Error; err != nil {
		t.Fatalf("find reply message: %v", err)
	}
	if stored.WorkflowRunID != 9988 {
		t.Fatalf("stored.WorkflowRunID=%d want 9988", stored.WorkflowRunID)
	}
}

func TestReplyCommitRejectsSensitiveModelOutput(t *testing.T) {
	db := setupReplyCommitTestDB(t)
	aiAgent := createReplyCommitTestAIAgent(t, db)
	conversation := createReplyCommitTestConversation(t, db, aiAgent.ID)
	_, err := newReplyCommitService().CommitAIReply(replyCommitInput{
		Conversation: *conversation, Message: models.Message{ID: 102, RequestID: "trace-102"}, AIAgent: *aiAgent,
		ReplyText: "authorization=Bearer-secret", ClientPrefix: "ai_reply",
	})
	if err == nil {
		t.Fatal("expected sensitive model output to be rejected")
	}
	var count int64
	if err := db.Model(&models.Message{}).Where("conversation_id = ?", conversation.ID).Count(&count).Error; err != nil {
		t.Fatalf("count messages: %v", err)
	}
	if count != 0 {
		t.Fatalf("unexpected message written for rejected output: %d", count)
	}
}

func setupReplyCommitTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	dbName := "reply_commit_test_" + strings.NewReplacer("/", "_").Replace(t.Name())
	db, err := gorm.Open(sqlite.Open("file:"+dbName+"?mode=memory&cache=shared"), &gorm.Config{
		NamingStrategy: schema.NamingStrategy{
			TablePrefix:   "t_",
			SingularTable: true,
		},
	})
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
		&models.AIAgent{},
		&models.Channel{},
		&models.ChannelMessageOutbox{},
		&models.Conversation{},
		&models.ConversationReadState{},
		&models.ConversationEventLog{},
		&models.Message{},
	); err != nil {
		t.Fatalf("auto migrate: %v", err)
	}
	sqls.SetDB(db)
	return db
}

func createReplyCommitTestAIAgent(t *testing.T, db *gorm.DB) *models.AIAgent {
	t.Helper()
	now := time.Now()
	item := &models.AIAgent{
		Name:   "reply-agent",
		Status: enums.StatusOk,
		AuditFields: models.AuditFields{
			CreatedAt: now,
			UpdatedAt: now,
		},
	}
	if err := db.Create(item).Error; err != nil {
		t.Fatalf("create ai agent: %v", err)
	}
	return item
}

func createReplyCommitTestConversation(t *testing.T, db *gorm.DB, aiAgentID int64) *models.Conversation {
	t.Helper()
	now := time.Now()
	item := &models.Conversation{
		CustomerID:   1,
		ChannelID:    11,
		AIAgentID:    aiAgentID,
		Status:       enums.IMConversationStatusAIServing,
		LastActiveAt: now,
		AuditFields: models.AuditFields{
			CreatedAt: now,
			UpdatedAt: now,
		},
	}
	if err := db.Create(item).Error; err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	return item
}
