package services

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"agent-desk/internal/ai/workflow/dsl"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

func TestAIWorkflowServiceValidateDefinitionReportsErrors(t *testing.T) {
	setupAIWorkflowTestDB(t)
	result := AIWorkflowService.ValidateDefinition(dsl.Definition{
		SchemaVersion: dsl.SchemaVersion,
		Nodes: []dsl.Node{
			workflowServiceTestNode("start_1", "start", nil, nil),
			workflowServiceTestNode("create_1", "create_ticket", nil, nil),
			workflowServiceTestNode("end_1", "end", nil, nil),
		},
		Edges: []dsl.Edge{
			workflowServiceTestEdge("start_1", "create_1"),
			workflowServiceTestEdge("create_1", "end_1"),
		},
	})

	if result.Valid {
		t.Fatalf("expected invalid workflow definition")
	}
	if len(result.Errors) == 0 {
		t.Fatalf("expected validation errors")
	}
}

func TestAIWorkflowServiceDefaultDefinitionUsesOfficialFlowGramModel(t *testing.T) {
	definition := defaultAgentWorkflowDefinition()
	if definition.SchemaVersion != 0 {
		t.Fatalf("official FlowGram definition must not contain the legacy schemaVersion, got %d", definition.SchemaVersion)
	}
	if len(definition.Nodes) != 3 {
		t.Fatalf("default node count = %d, want 3", len(definition.Nodes))
	}
	nodeTypes := []string{definition.Nodes[0].Type, definition.Nodes[1].Type, definition.Nodes[2].Type}
	if strings.Join(nodeTypes, ",") != "start,llm,end" {
		t.Fatalf("default node types = %v, want [start llm end]", nodeTypes)
	}
	if len(definition.GlobalVariable) == 0 {
		t.Fatalf("official FlowGram globalVariable is required")
	}
	if result := AIWorkflowService.ValidateDefinition(definition); !result.Valid {
		t.Fatalf("default official FlowGram definition is invalid: %#v", result.Errors)
	}
}

func TestAIWorkflowServicePublishCreatesImmutableVersion(t *testing.T) {
	setupAIWorkflowTestDB(t)
	operator := aiWorkflowTestOperator()
	workflow, err := AIWorkflowService.CreateWorkflow(request.CreateAIWorkflowRequest{
		Name:        "support flow",
		Description: "customer service flow",
		Definition:  validAIWorkflowDefinition(),
	}, operator)
	if err != nil {
		t.Fatalf("CreateWorkflow() error = %v", err)
	}

	version, err := AIWorkflowService.PublishWorkflow(request.PublishAIWorkflowRequest{
		WorkflowID: workflow.ID,
		Definition: validAIWorkflowDefinition(),
	}, operator)
	if err != nil {
		t.Fatalf("PublishWorkflow() error = %v", err)
	}

	if version.WorkflowID != workflow.ID {
		t.Fatalf("expected workflow id %d, got %d", workflow.ID, version.WorkflowID)
	}
	if version.Version != 1 {
		t.Fatalf("expected first version to be 1, got %d", version.Version)
	}
	if version.DefinitionHash == "" {
		t.Fatalf("expected definition hash")
	}
	if version.PublishedAt == nil {
		t.Fatalf("expected published timestamp")
	}

	var stored dsl.Definition
	if err := json.Unmarshal([]byte(version.Definition), &stored); err != nil {
		t.Fatalf("unmarshal stored definition: %v", err)
	}
	if stored.SchemaVersion != dsl.SchemaVersion || len(stored.Nodes) == 0 {
		t.Fatalf("unexpected stored definition: %+v", stored)
	}
}

func TestAIWorkflowServiceWorkflowTemplatesAreValid(t *testing.T) {
	templates := AIWorkflowService.ListWorkflowTemplates()
	if len(templates) != 4 {
		t.Fatalf("template count = %d, want 4", len(templates))
	}
	seen := make(map[string]struct{}, len(templates))
	for _, item := range templates {
		if item.Code == "" || item.Name == "" {
			t.Fatalf("template identity is required: %#v", item)
		}
		if _, exists := seen[item.Code]; exists {
			t.Fatalf("duplicate template code: %s", item.Code)
		}
		seen[item.Code] = struct{}{}
		if result := AIWorkflowService.ValidateDefinition(item.Definition); !result.Valid {
			t.Fatalf("template %s is invalid: %#v", item.Code, result.Errors)
		}
	}
}

func TestAIWorkflowServicePublishIncrementsVersion(t *testing.T) {
	setupAIWorkflowTestDB(t)
	operator := aiWorkflowTestOperator()
	workflow, err := AIWorkflowService.CreateWorkflow(request.CreateAIWorkflowRequest{
		Name:       "support flow versions",
		Definition: validAIWorkflowDefinition(),
	}, operator)
	if err != nil {
		t.Fatalf("CreateWorkflow() error = %v", err)
	}

	first, err := AIWorkflowService.PublishWorkflow(request.PublishAIWorkflowRequest{
		WorkflowID: workflow.ID,
		Definition: validAIWorkflowDefinition(),
	}, operator)
	if err != nil {
		t.Fatalf("PublishWorkflow() first error = %v", err)
	}
	second, err := AIWorkflowService.PublishWorkflow(request.PublishAIWorkflowRequest{
		WorkflowID: workflow.ID,
		Definition: validAIWorkflowDefinition(),
	}, operator)
	if err != nil {
		t.Fatalf("PublishWorkflow() second error = %v", err)
	}

	if first.Version != 1 || second.Version != 2 {
		t.Fatalf("expected versions 1 and 2, got %d and %d", first.Version, second.Version)
	}
}

func TestAIWorkflowServicePublishRejectsInvalidDSL(t *testing.T) {
	setupAIWorkflowTestDB(t)
	operator := aiWorkflowTestOperator()
	workflow, err := AIWorkflowService.CreateWorkflow(request.CreateAIWorkflowRequest{
		Name:       "invalid publish flow",
		Definition: validAIWorkflowDefinition(),
	}, operator)
	if err != nil {
		t.Fatalf("CreateWorkflow() error = %v", err)
	}

	_, err = AIWorkflowService.PublishWorkflow(request.PublishAIWorkflowRequest{
		WorkflowID: workflow.ID,
		Definition: dsl.Definition{
			SchemaVersion: dsl.SchemaVersion,
			Nodes: []dsl.Node{
				workflowServiceTestNode("start_1", "start", nil, nil),
				workflowServiceTestNode("create_1", "create_ticket", nil, nil),
				workflowServiceTestNode("end_1", "end", nil, nil),
			},
			Edges: []dsl.Edge{
				workflowServiceTestEdge("start_1", "create_1"),
				workflowServiceTestEdge("create_1", "end_1"),
			},
		},
	}, operator)
	if err == nil {
		t.Fatalf("expected invalid publish to fail")
	}
	if versions := repositories.AIWorkflowVersionRepository.Find(sqls.DB(), sqls.NewCnd().Eq("workflow_id", workflow.ID)); len(versions) != 0 {
		t.Fatalf("expected no versions after invalid publish, got %d", len(versions))
	}
}

func TestAIWorkflowServiceListExcludesDeletedWorkflows(t *testing.T) {
	setupAIWorkflowTestDB(t)
	operator := aiWorkflowTestOperator()
	active, err := AIWorkflowService.CreateWorkflow(request.CreateAIWorkflowRequest{
		Name:       "active workflow",
		Definition: validAIWorkflowDefinition(),
	}, operator)
	if err != nil {
		t.Fatalf("CreateWorkflow(active) error = %v", err)
	}
	deleted, err := AIWorkflowService.CreateWorkflow(request.CreateAIWorkflowRequest{
		Name:       "deleted workflow",
		Definition: validAIWorkflowDefinition(),
	}, operator)
	if err != nil {
		t.Fatalf("CreateWorkflow(deleted) error = %v", err)
	}
	if err := AIWorkflowService.DeleteWorkflow(deleted.ID, operator); err != nil {
		t.Fatalf("DeleteWorkflow() error = %v", err)
	}

	list, paging := AIWorkflowService.FindPageByCnd(sqls.NewCnd().NotEq("status", enums.StatusDeleted).Desc("id").Page(1, 20))
	if paging.Total != 1 || len(list) != 1 || list[0].ID != active.ID {
		t.Fatalf("deleted workflow must be excluded: total=%d list=%#v", paging.Total, list)
	}
}

func TestAIWorkflowServiceRunListAndDetail(t *testing.T) {
	setupAIWorkflowTestDB(t)
	now := time.Now()
	agent := models.AIAgent{Name: "售后 Agent", Status: enums.StatusOk}
	if err := sqls.DB().Create(&agent).Error; err != nil {
		t.Fatalf("create agent: %v", err)
	}
	workflow := models.AIWorkflow{Name: "售后流程", Status: enums.StatusOk}
	if err := sqls.DB().Create(&workflow).Error; err != nil {
		t.Fatalf("create workflow: %v", err)
	}
	versionDefinition := validAIWorkflowDefinition()
	versionDefinition.Nodes[1].Data.Title = "运行时回复"
	versionDefinitionJSON, err := json.Marshal(versionDefinition)
	if err != nil {
		t.Fatalf("marshal version definition: %v", err)
	}
	version := models.AIWorkflowVersion{
		WorkflowID: workflow.ID,
		Version:    7,
		Status:     enums.StatusOk,
		Definition: string(versionDefinitionJSON),
	}
	if err := sqls.DB().Create(&version).Error; err != nil {
		t.Fatalf("create workflow version: %v", err)
	}
	run := models.AIWorkflowRun{
		WorkflowID:        workflow.ID,
		WorkflowVersionID: version.ID,
		ConversationID:    303,
		AIAgentID:         agent.ID,
		MessageID:         404,
		Status:            1,
		StartedAt:         now,
		EndedAt:           &now,
	}
	if err := sqls.DB().Create(&run).Error; err != nil {
		t.Fatalf("create workflow run: %v", err)
	}
	otherRun := models.AIWorkflowRun{
		WorkflowID:        workflow.ID,
		WorkflowVersionID: version.ID,
		ConversationID:    999,
		AIAgentID:         agent.ID,
		MessageID:         505,
		Status:            1,
		StartedAt:         now,
	}
	if err := sqls.DB().Create(&otherRun).Error; err != nil {
		t.Fatalf("create other workflow run: %v", err)
	}
	nodes := []models.AIWorkflowNodeRun{
		{
			WorkflowRunID: run.ID,
			NodeID:        "start_1",
			NodeType:      "start",
			Status:        1,
			InputPreview:  `{"inputs":{}}`,
			OutputPreview: `{"messageId":404}`,
			StartedAt:     now,
			EndedAt:       &now,
		},
		{
			WorkflowRunID: run.ID,
			NodeID:        "reply_1",
			NodeType:      "llm_reply",
			Status:        1,
			OutputPreview: `{"replyText":"hello"}`,
			StartedAt:     now,
			EndedAt:       &now,
			DurationMS:    8,
		},
	}
	if err := sqls.DB().Create(&nodes).Error; err != nil {
		t.Fatalf("create workflow node runs: %v", err)
	}

	list, paging := AIWorkflowService.FindRunPageByCnd(sqls.NewCnd().Eq("conversation_id", 303).Desc("id").Page(1, 20))
	if paging.Total != 1 || len(list) != 1 || list[0].ID != run.ID {
		t.Fatalf("unexpected run list: total=%d list=%#v", paging.Total, list)
	}
	auditItems := AIWorkflowService.BuildRunAuditItems(list)
	if len(auditItems) != 1 {
		t.Fatalf("unexpected audit item count: %d", len(auditItems))
	}
	if auditItems[0].Workflow == nil || auditItems[0].Workflow.Name != workflow.Name {
		t.Fatalf("expected workflow context, got %#v", auditItems[0].Workflow)
	}
	if auditItems[0].Version == nil || auditItems[0].Version.Version != version.Version {
		t.Fatalf("expected version context, got %#v", auditItems[0].Version)
	}
	if auditItems[0].Agent == nil || auditItems[0].Agent.Name != agent.Name {
		t.Fatalf("expected agent context, got %#v", auditItems[0].Agent)
	}

	detail, nodeRuns := AIWorkflowService.GetRunDetail(run.ID)
	if detail == nil || detail.ID != run.ID {
		t.Fatalf("unexpected detail run: %#v", detail)
	}
	if len(nodeRuns) != 2 || nodeRuns[0].NodeID != "start_1" || nodeRuns[1].NodeID != "reply_1" {
		t.Fatalf("unexpected detail nodes: %#v", nodeRuns)
	}
	if missing, missingNodes := AIWorkflowService.GetRunDetail(999999); missing != nil || len(missingNodes) != 0 {
		t.Fatalf("expected missing detail to be empty, got run=%#v nodes=%#v", missing, missingNodes)
	}
}

func setupAIWorkflowTestDB(t *testing.T) {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite db: %v", err)
	}
	if err := db.AutoMigrate(&models.AIAgent{}, &models.AIWorkflow{}, &models.AIWorkflowVersion{}, &models.AIAgentWorkflowBinding{}, &models.AIWorkflowRun{}, &models.AIWorkflowNodeRun{}); err != nil {
		t.Fatalf("auto migrate: %v", err)
	}
	sqls.SetDB(db)
	for _, id := range []int64{12, 23, 99} {
		if err := sqls.DB().Create(&models.AIAgent{ID: id, Name: "agent", Status: enums.StatusOk}).Error; err != nil {
			t.Fatalf("create ai agent: %v", err)
		}
	}
}

func validAIWorkflowDefinition() dsl.Definition {
	return dsl.Definition{
		SchemaVersion: dsl.SchemaVersion,
		Nodes: []dsl.Node{
			workflowServiceTestNode("start_1", "start", nil, nil),
			workflowServiceTestNode("reply_1", "send_reply", map[string]dsl.Value{
				"replyText": dsl.RefValue("start_1", "userMessage"),
			}, map[string]any{"text": "hello"}),
			workflowServiceTestNode("end_1", "end", nil, nil),
		},
		Edges: []dsl.Edge{
			workflowServiceTestEdge("start_1", "reply_1"),
			workflowServiceTestEdge("reply_1", "end_1"),
		},
	}
}

func workflowServiceTestNode(id string, nodeType string, inputs map[string]dsl.Value, config any) dsl.Node {
	return dsl.Node{
		ID:   id,
		Type: nodeType,
		Meta: dsl.NodeMeta{Position: dsl.Position{X: 0, Y: 0}},
		Data: dsl.NodeData{
			Title:        nodeType,
			InputsValues: inputs,
			Config:       mustMarshalWorkflowServiceTestConfig(config),
		},
	}
}

func workflowServiceTestEdge(source string, target string) dsl.Edge {
	return dsl.Edge{SourceNodeID: source, TargetNodeID: target}
}

func mustMarshalWorkflowServiceTestConfig(value any) json.RawMessage {
	if value == nil {
		return nil
	}
	raw, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	return raw
}

func aiWorkflowTestOperator() *dto.AuthPrincipal {
	return &dto.AuthPrincipal{
		UserID:   1,
		Username: "workflow-tester",
		Nickname: "workflow-tester",
	}
}
