package services

import (
	"strings"
	"testing"
	"time"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/httpx/params"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
	"gorm.io/gorm/schema"
)

func TestAgentRunServiceFindsWorkflowAuditDetail(t *testing.T) {
	db := setupAgentRunServiceTestDB(t)
	now := time.Now()
	endedAt := now.Add(time.Second)
	run := &models.AgentRun{
		ConversationID: 11,
		AIAgentID:      12,
		WorkflowRunID:  13,

		Status:    "completed",
		StartedAt: now,
		EndedAt:   &endedAt,
		CreatedAt: now,
		UpdatedAt: now,
	}
	if err := db.Create(run).Error; err != nil {
		t.Fatalf("create agent run: %v", err)
	}
	if err := db.Create(&models.AgentStep{AgentRunID: run.ID, StepType: "workflow", Status: "completed", StartedAt: now, EndedAt: &endedAt, CreatedAt: now}).Error; err != nil {
		t.Fatalf("create agent step: %v", err)
	}
	if err := db.Create(&models.AgentToolCall{AgentRunID: run.ID, ToolCode: "knowledge.retrieve", Status: "completed", CreatedAt: now}).Error; err != nil {
		t.Fatalf("create tool call: %v", err)
	}

	cnd := sqls.NewCnd().Eq("conversation_id", run.ConversationID).Desc("id").Page(1, 20)
	queryParams := &params.QueryParams{Cnd: *cnd}
	list, paging := AgentRunService.FindPageByParams(queryParams)
	if len(list) != 1 || paging.Total != 1 || list[0].ID != run.ID {
		t.Fatalf("unexpected agent run page: list=%#v paging=%#v", list, paging)
	}
	item, steps, toolCalls := AgentRunService.GetDetail(run.ID)
	if item == nil || len(steps) != 1 || len(toolCalls) != 1 {
		t.Fatalf("unexpected agent run detail: run=%#v steps=%#v toolCalls=%#v", item, steps, toolCalls)
	}
}

func TestAgentRunServiceRecordsAgentLoopToolCall(t *testing.T) {
	db := setupAgentRunServiceTestDB(t)
	now := time.Now()
	runID, err := AgentRunService.RecordAgentLoopRun(db, AgentLoopRunInput{
		ConversationID: 1, AIAgentID: 2, AgentRevisionID: 3, Status: "completed", StartedAt: now,
		StepType: "model", StepCode: "chat_completion", StepInputPreview: "authorization=Bearer-secret", ToolCalls: []AgentLoopToolCallInput{{
			ToolCode: "knowledge/search", RiskLevel: "read", Status: "completed", ArgumentsPreview: `{"token":"abc123","query":"refund"}`, ResultPreview: "policy text",
		}},
	})
	if err != nil {
		t.Fatalf("RecordAgentLoopRun returned error: %v", err)
	}
	_, steps, toolCalls := AgentRunService.GetDetail(runID)
	if len(toolCalls) != 1 || toolCalls[0].ToolCode != "knowledge/search" || toolCalls[0].AgentStepID <= 0 {
		t.Fatalf("unexpected tool audit: %#v", toolCalls)
	}
	if strings.Contains(toolCalls[0].ArgumentsPreview, "abc123") || len(steps) != 1 || strings.Contains(steps[0].InputPreview, "Bearer-secret") {
		t.Fatalf("sensitive audit data leaked: steps=%#v calls=%#v", steps, toolCalls)
	}
}

func TestAgentRunServiceRecordsResumedToolCall(t *testing.T) {
	db := setupAgentRunServiceTestDB(t)
	now := time.Now()
	run := &models.AgentRun{Status: "interrupted", StartedAt: now, CreatedAt: now, UpdatedAt: now}
	if err := db.Create(run).Error; err != nil {
		t.Fatalf("create interrupted run: %v", err)
	}
	err := AgentRunService.RecordResume(db, run.ID, 0, "completed", "操作已执行", &AgentLoopToolCallInput{
		ToolCode: "crm/update_customer", RiskLevel: "write", RequireConfirm: true,
		Status: "completed", ArgumentsPreview: `{"name":"Ada"}`, ResultPreview: "updated",
	})
	if err != nil {
		t.Fatalf("RecordResume returned error: %v", err)
	}
	item, steps, toolCalls := AgentRunService.GetDetail(run.ID)
	if item == nil || item.Status != "completed" || len(steps) != 1 || steps[0].StepType != "resume" {
		t.Fatalf("unexpected resumed run audit: item=%#v steps=%#v", item, steps)
	}
	if len(toolCalls) != 1 || toolCalls[0].AgentStepID != steps[0].ID || !toolCalls[0].RequireConfirm || toolCalls[0].Status != "completed" {
		t.Fatalf("unexpected resumed tool audit: %#v", toolCalls)
	}
}

func TestAgentRunServiceSavesQualityFeedbackPerRun(t *testing.T) {
	db := setupAgentRunServiceTestDB(t)
	now := time.Now()
	run := &models.AgentRun{AIAgentID: 4, Status: "completed", StartedAt: now, CreatedAt: now, UpdatedAt: now}
	if err := db.Create(run).Error; err != nil {
		t.Fatalf("create agent run: %v", err)
	}
	operator := &dto.AuthPrincipal{UserID: 7, Username: "reviewer"}
	if err := AgentRunService.SaveQualityFeedback(request.SaveAgentRunQualityFeedbackRequest{
		AgentRunID: run.ID, ResolutionStatus: enums.AgentRunResolutionStatusResolved, EvidenceStatus: enums.AgentRunEvidenceStatusSupported, Comment: "issue resolved",
	}, operator); err != nil {
		t.Fatalf("save quality feedback: %v", err)
	}
	if err := AgentRunService.SaveQualityFeedback(request.SaveAgentRunQualityFeedbackRequest{
		AgentRunID: run.ID, ResolutionStatus: enums.AgentRunResolutionStatusUnresolved, EvidenceStatus: enums.AgentRunEvidenceStatusUnsupported, Comment: "missing evidence",
	}, operator); err != nil {
		t.Fatalf("update quality feedback: %v", err)
	}
	feedback := AgentRunService.GetQualityFeedback(run.ID)
	if feedback == nil || feedback.ResolutionStatus != enums.AgentRunResolutionStatusUnresolved || feedback.EvidenceStatus != enums.AgentRunEvidenceStatusUnsupported || feedback.Comment != "missing evidence" || feedback.UpdateUserName != "reviewer" {
		t.Fatalf("unexpected quality feedback: %#v", feedback)
	}
}

func TestAgentRunServiceAggregatesMetrics(t *testing.T) {
	db := setupAgentRunServiceTestDB(t)
	base := time.Now().Add(-time.Minute)
	runs := []models.AgentRun{
		{AIAgentID: 8, Status: "completed", StartedAt: base, EndedAt: timePtr(base.Add(100 * time.Millisecond)), PromptTokens: 10, CompletionTokens: 5, CreatedAt: base, UpdatedAt: base},
		{AIAgentID: 8, Status: "failed", StartedAt: base, EndedAt: timePtr(base.Add(300 * time.Millisecond)), PromptTokens: 8, CompletionTokens: 2, CreatedAt: base, UpdatedAt: base},
		{AIAgentID: 9, Status: "completed", StartedAt: base, EndedAt: timePtr(base.Add(900 * time.Millisecond)), CreatedAt: base, UpdatedAt: base},
	}
	for index := range runs {
		if err := db.Create(&runs[index]).Error; err != nil {
			t.Fatalf("create run: %v", err)
		}
	}
	if err := db.Create(&models.AgentStep{AgentRunID: runs[0].ID, Status: "completed", StartedAt: base, CreatedAt: base}).Error; err != nil {
		t.Fatalf("create step: %v", err)
	}
	if err := db.Create(&models.AgentStep{AgentRunID: runs[1].ID, Status: "failed", StartedAt: base, CreatedAt: base}).Error; err != nil {
		t.Fatalf("create step: %v", err)
	}
	if err := db.Create(&models.AgentToolCall{AgentRunID: runs[0].ID, Status: "completed", CreatedAt: base}).Error; err != nil {
		t.Fatalf("create completed tool call: %v", err)
	}
	if err := db.Create(&models.AgentToolCall{AgentRunID: runs[1].ID, Status: "failed", CreatedAt: base}).Error; err != nil {
		t.Fatalf("create failed tool call: %v", err)
	}
	if err := db.Create(&models.Conversation{AIAgentID: 8}).Error; err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	handoffAt := base
	if err := db.Create(&models.Conversation{AIAgentID: 8, HandoffAt: &handoffAt}).Error; err != nil {
		t.Fatalf("create handoff conversation: %v", err)
	}
	if err := db.Create(&models.ConversationInterrupt{AgentRunID: runs[0].ID, CheckPointID: "metrics-resolved", Status: "resolved", ResumeCount: 1, CreatedAt: base, UpdatedAt: base}).Error; err != nil {
		t.Fatalf("create resolved interrupt: %v", err)
	}
	if err := db.Create(&models.ConversationInterrupt{AgentRunID: runs[1].ID, CheckPointID: "metrics-cancelled", Status: "cancelled", ResumeCount: 1, CreatedAt: base, UpdatedAt: base}).Error; err != nil {
		t.Fatalf("create cancelled interrupt: %v", err)
	}
	if err := db.Create(&models.AgentRunQualityFeedback{AgentRunID: runs[0].ID, ResolutionStatus: enums.AgentRunResolutionStatusResolved, EvidenceStatus: enums.AgentRunEvidenceStatusSupported}).Error; err != nil {
		t.Fatalf("create resolved feedback: %v", err)
	}
	if err := db.Create(&models.AgentRunQualityFeedback{AgentRunID: runs[1].ID, ResolutionStatus: enums.AgentRunResolutionStatusUnresolved, EvidenceStatus: enums.AgentRunEvidenceStatusUnsupported}).Error; err != nil {
		t.Fatalf("create unresolved feedback: %v", err)
	}
	metrics := AgentRunService.GetMetrics(8)
	if metrics.TotalRuns != 2 || metrics.CompletedRuns != 1 || metrics.FailedRuns != 1 || metrics.CompletionRate != 0.5 {
		t.Fatalf("unexpected run metrics: %#v", metrics)
	}
	if metrics.AverageDurationMS != 200 || metrics.P95DurationMS != 300 || metrics.ToolCalls != 2 || metrics.ToolSuccessRate != 0.5 || metrics.AverageSteps != 1 {
		t.Fatalf("unexpected aggregate metrics: %#v", metrics)
	}
	if metrics.PromptTokens != 18 || metrics.CompletionTokens != 7 {
		t.Fatalf("unexpected token metrics: %#v", metrics)
	}
	if metrics.HandoffRate != 0.5 || metrics.KnowledgeFallbackRate != 0 {
		t.Fatalf("unexpected business metrics: %#v", metrics)
	}
	if metrics.ResumedInterrupts != 2 || metrics.ResolvedInterrupts != 1 || metrics.InterruptRecoveryRate != 0.5 {
		t.Fatalf("unexpected interrupt recovery metrics: %#v", metrics)
	}
	if metrics.ReviewedRuns != 2 || metrics.ResolvedRuns != 1 || metrics.ResolutionRate != 0.5 || metrics.UnsupportedEvidenceRuns != 1 || metrics.UnsupportedEvidenceRate != 0.5 {
		t.Fatalf("unexpected quality metrics: %#v", metrics)
	}
}

func timePtr(value time.Time) *time.Time { return &value }

func setupAgentRunServiceTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	dbName := strings.NewReplacer("/", "_", " ", "_").Replace(t.Name())
	db, err := gorm.Open(sqlite.Open("file:"+dbName+"?mode=memory&cache=shared"), &gorm.Config{
		NamingStrategy: schema.NamingStrategy{TablePrefix: "t_", SingularTable: true},
	})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	t.Cleanup(func() {
		sqlDB, err := db.DB()
		if err == nil {
			_ = sqlDB.Close()
		}
	})
	if err := db.AutoMigrate(&models.AgentRevision{}, &models.AgentRun{}, &models.AgentStep{}, &models.AgentToolCall{}, &models.AgentRunQualityFeedback{}, &models.Conversation{}, &models.ConversationInterrupt{}); err != nil {
		t.Fatalf("auto migrate: %v", err)
	}
	sqls.SetDB(db)
	return db
}
