package services

import (
	"encoding/json"
	"strings"
	"testing"

	"agent-desk/internal/ai/workflow/dsl"
	workflowregistry "agent-desk/internal/ai/workflow/registry"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

func TestBuildKnowledgeBaseModelUsesLowerDefaultScoreThreshold(t *testing.T) {
	item, err := KnowledgeBaseService.buildKnowledgeBaseModel(request.CreateKnowledgeBaseRequest{})
	if err != nil {
		t.Fatalf("build knowledge base model failed: %v", err)
	}
	if item.DefaultScoreThreshold != 0.2 {
		t.Fatalf("expected default score threshold 0.2, got %v", item.DefaultScoreThreshold)
	}
}

func TestDeleteKnowledgeBaseRejectsWorkflowDraftReference(t *testing.T) {
	setupKnowledgeBaseServiceTestDB(t)
	kb := createKnowledgeBaseServiceTestBase(t, "Referenced KB")
	otherKB := createKnowledgeBaseServiceTestBase(t, "Other KB")
	createKnowledgeBaseServiceTestWorkflow(t, "Support Workflow", knowledgeBaseServiceTestWorkflowDefinition([]int64{12, otherKB.ID}))
	createKnowledgeBaseServiceTestWorkflow(t, "Knowledge Workflow", knowledgeBaseServiceTestWorkflowDefinition([]int64{12, kb.ID, otherKB.ID}))

	err := KnowledgeBaseService.DeleteKnowledgeBase(kb.ID)
	if err == nil {
		t.Fatal("DeleteKnowledgeBase() error is nil, want referenced workflow error")
	}
	if got := err.Error(); !strings.Contains(got, "Knowledge Workflow") {
		t.Fatalf("DeleteKnowledgeBase() error = %q, want workflow name", got)
	}
	if repositories.KnowledgeBaseRepository.Get(sqls.DB(), kb.ID) == nil {
		t.Fatal("knowledge base was deleted despite workflow reference")
	}
}

func TestDeleteKnowledgeBaseRejectsWorkflowVersionReference(t *testing.T) {
	setupKnowledgeBaseServiceTestDB(t)
	kb := createKnowledgeBaseServiceTestBase(t, "Version KB")
	workflow := createKnowledgeBaseServiceTestWorkflow(t, "Published Workflow", knowledgeBaseServiceTestWorkflowDefinition([]int64{999}))
	raw, err := json.Marshal(knowledgeBaseServiceTestWorkflowDefinition([]int64{kb.ID}))
	if err != nil {
		t.Fatalf("marshal workflow version definition: %v", err)
	}
	if err := repositories.AIWorkflowVersionRepository.Create(sqls.DB(), &models.AIWorkflowVersion{
		WorkflowID: workflow.ID,
		Version:    1,
		Status:     enums.StatusOk,
		Definition: string(raw),
	}); err != nil {
		t.Fatalf("create workflow version: %v", err)
	}

	err = KnowledgeBaseService.DeleteKnowledgeBase(kb.ID)
	if err == nil {
		t.Fatal("DeleteKnowledgeBase() error is nil, want referenced workflow version error")
	}
	if got := err.Error(); !strings.Contains(got, "Published Workflow") {
		t.Fatalf("DeleteKnowledgeBase() error = %q, want workflow name", got)
	}
}

func TestDeleteKnowledgeBaseCascadesContentWhenNotReferenced(t *testing.T) {
	setupKnowledgeBaseServiceTestDB(t)
	kb := createKnowledgeBaseServiceTestBase(t, "Delete KB")
	document := &models.KnowledgeDocument{
		KnowledgeBaseID: kb.ID,
		Title:           "Doc",
		ContentType:     enums.KnowledgeDocumentContentTypeMarkdown,
		Content:         "content",
		Status:          enums.StatusOk,
		IndexStatus:     enums.KnowledgeDocumentIndexStatusIndexed,
	}
	if err := repositories.KnowledgeDocumentRepository.Create(sqls.DB(), document); err != nil {
		t.Fatalf("create document: %v", err)
	}
	faq := &models.KnowledgeFAQ{
		KnowledgeBaseID: kb.ID,
		Question:        "Question",
		Answer:          "Answer",
		Status:          enums.StatusOk,
		IndexStatus:     enums.KnowledgeDocumentIndexStatusIndexed,
	}
	if err := repositories.KnowledgeFAQRepository.Create(sqls.DB(), faq); err != nil {
		t.Fatalf("create faq: %v", err)
	}
	if err := repositories.KnowledgeChunkRepository.BatchCreate(sqls.DB(), []models.KnowledgeChunk{
		{KnowledgeBaseID: kb.ID, DocumentID: document.ID, ChunkNo: 1, Status: enums.StatusOk},
		{KnowledgeBaseID: kb.ID, FaqID: faq.ID, ChunkNo: 1, Status: enums.StatusOk},
	}); err != nil {
		t.Fatalf("create chunks: %v", err)
	}

	if err := KnowledgeBaseService.DeleteKnowledgeBase(kb.ID); err != nil {
		t.Fatalf("DeleteKnowledgeBase() error = %v", err)
	}

	assertKnowledgeBaseServiceTestCount(t, &models.KnowledgeBase{}, "id = ?", kb.ID, 0)
	assertKnowledgeBaseServiceTestCount(t, &models.KnowledgeDocument{}, "knowledge_base_id = ?", kb.ID, 0)
	assertKnowledgeBaseServiceTestCount(t, &models.KnowledgeFAQ{}, "knowledge_base_id = ?", kb.ID, 0)
	assertKnowledgeBaseServiceTestCount(t, &models.KnowledgeChunk{}, "knowledge_base_id = ?", kb.ID, 0)
}

func setupKnowledgeBaseServiceTestDB(t *testing.T) {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite db: %v", err)
	}
	if err := db.AutoMigrate(&models.KnowledgeBase{}, &models.KnowledgeDocument{}, &models.KnowledgeFAQ{}, &models.KnowledgeChunk{}, &models.AIAgent{}, &models.AIWorkflow{}, &models.AIWorkflowVersion{}); err != nil {
		t.Fatalf("auto migrate: %v", err)
	}
	sqls.SetDB(db)
}

func createKnowledgeBaseServiceTestWorkflow(t *testing.T, name string, definition dsl.Definition) *models.AIWorkflow {
	t.Helper()
	raw, err := json.Marshal(definition)
	if err != nil {
		t.Fatalf("marshal workflow definition: %v", err)
	}
	item := &models.AIWorkflow{
		Name:            name,
		Status:          enums.StatusOk,
		DraftDefinition: string(raw),
	}
	if err := repositories.AIWorkflowRepository.Create(sqls.DB(), item); err != nil {
		t.Fatalf("create workflow: %v", err)
	}
	return item
}

func knowledgeBaseServiceTestWorkflowDefinition(knowledgeBaseIDs []int64) dsl.Definition {
	return dsl.Definition{
		SchemaVersion: dsl.SchemaVersion,
		Nodes: []dsl.Node{
			{
				ID:   "start_1",
				Type: workflowregistry.NodeTypeStart,
			},
			{
				ID:   "retrieve_1",
				Type: workflowregistry.NodeTypeKnowledgeRetrieve,
				Data: dsl.NodeData{
					Config: mustKnowledgeBaseServiceTestJSON(map[string]any{"knowledgeBaseIds": knowledgeBaseIDs}),
					InputsValues: map[string]dsl.Value{
						"query": dsl.RefValue("start_1", "userMessage"),
					},
				},
			},
			{
				ID:   "end_1",
				Type: workflowregistry.NodeTypeEnd,
			},
		},
		Edges: []dsl.Edge{
			{SourceNodeID: "start_1", TargetNodeID: "retrieve_1"},
			{SourceNodeID: "retrieve_1", TargetNodeID: "end_1"},
		},
	}
}

func mustKnowledgeBaseServiceTestJSON(value any) json.RawMessage {
	raw, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	return raw
}

func createKnowledgeBaseServiceTestBase(t *testing.T, name string) *models.KnowledgeBase {
	t.Helper()
	item := &models.KnowledgeBase{
		Name:          name,
		KnowledgeType: string(enums.KnowledgeBaseTypeDocument),
		Status:        enums.StatusOk,
	}
	if err := repositories.KnowledgeBaseRepository.Create(sqls.DB(), item); err != nil {
		t.Fatalf("create knowledge base: %v", err)
	}
	return item
}

func assertKnowledgeBaseServiceTestCount(t *testing.T, model any, query string, arg any, want int64) {
	t.Helper()
	var count int64
	if err := sqls.DB().Model(model).Where(query, arg).Count(&count).Error; err != nil {
		t.Fatalf("count %T: %v", model, err)
	}
	if count != want {
		t.Fatalf("count %T = %d, want %d", model, count, want)
	}
}
