package services

import (
	"encoding/json"
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

func TestAIHandoffPublishesFinalAssignedConversationEvent(t *testing.T) {
	db := setupHumanDispatchRealtimeTestDB(t)
	WsService = newWsService()
	session := captureHumanDispatchRealtimeSession(t, "admin:101", "admin:all")
	aiAgent := createHumanDispatchRealtimeAIAgent(t, db, "1")
	createHumanDispatchRealtimeTeam(t, db, 1)
	createHumanDispatchRealtimeActiveSchedule(t, db, 1)
	createHumanDispatchRealtimeAgentProfile(t, db, 101, 1)
	conversation := createHumanDispatchRealtimeConversation(t, db, aiAgent.ID)

	result, err := ConversationHumanDispatchService.HandoffByAI(conversation.ID, aiAgent, "用户要求转人工")
	if err != nil {
		t.Fatalf("HandoffByAI() error = %v", err)
	}
	if result == nil || result.Decision != HandoffDecisionAssigned {
		t.Fatalf("expected assigned decision, got %+v", result)
	}

	event := findHumanDispatchRealtimeEvent(t, session, enums.IMRealtimeEventConversationAssigned)
	if event.Data["conversationId"] != float64(conversation.ID) {
		t.Fatalf("unexpected conversation id in event: %+v", event.Data)
	}
	if event.Data["status"] != float64(enums.IMConversationStatusActive) {
		t.Fatalf("expected active status in assigned event, got %+v", event.Data["status"])
	}
	if event.Data["currentAssigneeId"] != float64(101) {
		t.Fatalf("expected assignee 101 in assigned event, got %+v", event.Data["currentAssigneeId"])
	}
}

func TestAIHandoffPublishesFinalTeamPoolConversationEvent(t *testing.T) {
	db := setupHumanDispatchRealtimeTestDB(t)
	WsService = newWsService()
	session := captureHumanDispatchRealtimeSession(t, "admin:all")
	aiAgent := createHumanDispatchRealtimeAIAgent(t, db, "1")
	createHumanDispatchRealtimeTeam(t, db, 1)
	createHumanDispatchRealtimeActiveSchedule(t, db, 1)
	conversation := createHumanDispatchRealtimeConversation(t, db, aiAgent.ID)

	result, err := ConversationHumanDispatchService.HandoffByAI(conversation.ID, aiAgent, "用户要求转人工")
	if err != nil {
		t.Fatalf("HandoffByAI() error = %v", err)
	}
	if result == nil || result.Decision != HandoffDecisionTeamPool {
		t.Fatalf("expected team_pool decision, got %+v", result)
	}

	event := findHumanDispatchRealtimeEvent(t, session, enums.IMRealtimeEventConversationUpdated, func(event humanDispatchRealtimeEvent) bool {
		return event.Data["currentTeamId"] == float64(1)
	})
	if event.Data["conversationId"] != float64(conversation.ID) {
		t.Fatalf("unexpected conversation id in event: %+v", event.Data)
	}
	if event.Data["status"] != float64(enums.IMConversationStatusPending) {
		t.Fatalf("expected pending status in updated event, got %+v", event.Data["status"])
	}
	if value, ok := event.Data["currentAssigneeId"]; ok && value != float64(0) {
		t.Fatalf("expected no assignee in updated event, got %+v", event.Data["currentAssigneeId"])
	}
}

type humanDispatchRealtimeEvent struct {
	Type string         `json:"type"`
	Data map[string]any `json:"data"`
}

func findHumanDispatchRealtimeEvent(t *testing.T, session *ClientSession, eventType string, matchers ...func(humanDispatchRealtimeEvent) bool) humanDispatchRealtimeEvent {
	t.Helper()
	timeout := time.After(500 * time.Millisecond)
	for {
		select {
		case raw := <-session.Send:
			var event humanDispatchRealtimeEvent
			if err := json.Unmarshal(raw, &event); err != nil {
				t.Fatalf("decode realtime event: %v", err)
			}
			if event.Type != eventType {
				continue
			}
			matched := true
			for _, matcher := range matchers {
				if !matcher(event) {
					matched = false
					break
				}
			}
			if matched {
				return event
			}
		case <-timeout:
			t.Fatalf("expected realtime event %q", eventType)
		}
	}
}

func captureHumanDispatchRealtimeSession(t *testing.T, topics ...string) *ClientSession {
	t.Helper()
	session := &ClientSession{
		ID:     "test-session",
		Role:   realtimeRoleAdmin,
		Topics: map[string]struct{}{},
		Send:   make(chan []byte, 32),
	}
	WsService.manager.Register(session, topics)
	return session
}

func setupHumanDispatchRealtimeTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	dbName := strings.NewReplacer("/", "_", " ", "_").Replace(t.Name())
	db, err := gorm.Open(sqlite.Open("file:"+dbName+"?mode=memory&cache=shared"), &gorm.Config{
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
	if err := db.AutoMigrate(
		&models.User{},
		&models.Notification{},
		&models.Customer{},
		&models.CustomerIdentity{},
		&models.Channel{},
		&models.AIAgent{},
		&models.AgentTeam{},
		&models.AgentTeamSchedule{},
		&models.AgentProfile{},
		&models.Conversation{},
		&models.ConversationParticipant{},
		&models.ConversationAssignment{},
		&models.ConversationEventLog{},
		&models.ConversationReadState{},
		&models.Message{},
		&models.ChannelMessageOutbox{},
	); err != nil {
		t.Fatalf("auto migrate error = %v", err)
	}
	sqls.SetDB(db)
	return db
}

func createHumanDispatchRealtimeAIAgent(t *testing.T, db *gorm.DB, teamIDs string) models.AIAgent {
	t.Helper()
	item := models.AIAgent{
		Name:        "测试AI",
		ServiceMode: enums.IMConversationServiceModeAIFirst,
		TeamIDs:     teamIDs,
		Status:      enums.StatusOk,
	}
	if err := db.Create(&item).Error; err != nil {
		t.Fatalf("create ai agent error = %v", err)
	}
	return item
}

func createHumanDispatchRealtimeTeam(t *testing.T, db *gorm.DB, id int64) {
	t.Helper()
	if err := db.Create(&models.AgentTeam{ID: id, Name: "售后支持组", Status: enums.StatusOk}).Error; err != nil {
		t.Fatalf("create team error = %v", err)
	}
}

func createHumanDispatchRealtimeActiveSchedule(t *testing.T, db *gorm.DB, teamID int64) {
	t.Helper()
	now := time.Now()
	if err := db.Create(&models.AgentTeamSchedule{
		TeamID:  teamID,
		StartAt: now.Add(-time.Hour),
		EndAt:   now.Add(time.Hour),
		Status:  enums.StatusOk,
	}).Error; err != nil {
		t.Fatalf("create schedule error = %v", err)
	}
}

func createHumanDispatchRealtimeAgentProfile(t *testing.T, db *gorm.DB, userID, teamID int64) {
	t.Helper()
	if err := db.Create(&models.User{
		ID:       userID,
		Username: "agent",
		Nickname: "客服",
		Status:   enums.StatusOk,
	}).Error; err != nil {
		t.Fatalf("create user error = %v", err)
	}
	if err := db.Create(&models.AgentProfile{
		UserID:             userID,
		TeamID:             teamID,
		AgentCode:          "A001",
		DisplayName:        "客服",
		ServiceStatus:      enums.ServiceStatusIdle,
		MaxConcurrentCount: 3,
		AutoAssignEnabled:  true,
		Status:             enums.StatusOk,
	}).Error; err != nil {
		t.Fatalf("create profile error = %v", err)
	}
}

func createHumanDispatchRealtimeConversation(t *testing.T, db *gorm.DB, aiAgentID int64) models.Conversation {
	t.Helper()
	now := time.Now()
	item := models.Conversation{
		AIAgentID:     aiAgentID,
		ChannelID:     1,
		CustomerID:    1,
		CustomerName:  "测试访客",
		Status:        enums.IMConversationStatusAIServing,
		ServiceMode:   enums.IMConversationServiceModeAIFirst,
		LastMessageAt: now,
		LastActiveAt:  now,
	}
	if err := db.Create(&item).Error; err != nil {
		t.Fatalf("create conversation error = %v", err)
	}
	return item
}
