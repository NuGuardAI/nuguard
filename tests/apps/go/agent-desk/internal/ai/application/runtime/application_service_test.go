package runtime

import (
	"strings"
	"testing"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

func TestAgentApplicationServiceLoadsConsistentPersistedRequest(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+strings.ReplaceAll(t.Name(), "/", "_")+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if err := db.AutoMigrate(&models.AIConfig{}, &models.AIAgent{}, &models.Conversation{}, &models.Message{}); err != nil {
		t.Fatalf("auto migrate: %v", err)
	}
	sqls.SetDB(db)
	config := &models.AIConfig{Status: enums.StatusOk, ModelName: "test-model"}
	if err := db.Create(config).Error; err != nil {
		t.Fatalf("create config: %v", err)
	}
	agent := &models.AIAgent{Name: "agent", Status: enums.StatusOk, AIConfigID: config.ID}
	if err := db.Create(agent).Error; err != nil {
		t.Fatalf("create agent: %v", err)
	}
	conversation := &models.Conversation{AIAgentID: agent.ID}
	if err := db.Create(conversation).Error; err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	message := &models.Message{ConversationID: conversation.ID, SenderType: enums.IMSenderTypeCustomer, MessageType: enums.IMMessageTypeText, Content: "hello"}
	if err := db.Create(message).Error; err != nil {
		t.Fatalf("create message: %v", err)
	}
	req, err := NewAgentApplicationService().loadRequest(ApplicationRunInput{ConversationID: conversation.ID, MessageID: message.ID, AIAgentID: agent.ID})
	if err != nil {
		t.Fatalf("loadRequest: %v", err)
	}
	if req.Conversation.ID != conversation.ID || req.UserMessage.ID != message.ID || req.AIAgent.ID != agent.ID || req.AIConfig.ID != config.ID {
		t.Fatalf("unexpected request: %#v", req)
	}
}

func TestAgentApplicationServiceRejectsMismatchedMessage(t *testing.T) {
	service := NewAgentApplicationService()
	if _, err := service.loadRequest(ApplicationRunInput{ConversationID: 1, MessageID: 0, AIAgentID: 1}); err == nil {
		t.Fatal("expected invalid identifiers error")
	}
}
