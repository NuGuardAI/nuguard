package builders

import (
	"encoding/json"
	"testing"
	"time"

	"agent-desk/internal/ai/workflow/dsl"
	workflowregistry "agent-desk/internal/ai/workflow/registry"
	"agent-desk/internal/models"
)

func TestBuildAIWorkflowNodeSpecsIncludesVariableContracts(t *testing.T) {
	specs := BuildAIWorkflowNodeSpecs(workflowregistry.DefaultRegistry().List())

	var startFound bool
	var sendReplyFound bool
	for _, spec := range specs {
		switch spec.Type {
		case workflowregistry.NodeTypeStart:
			startFound = true
			if spec.Icon != "PlayCircleIcon" {
				t.Fatalf("expected start icon PlayCircleIcon, got %q", spec.Icon)
			}
			if !hasResponseVariable(spec.OutputSchema, "userMessage") {
				t.Fatalf("expected start output userMessage, got %#v", spec.OutputSchema)
			}
		case workflowregistry.NodeTypeSendReply:
			sendReplyFound = true
			if !hasResponseVariable(spec.InputSchema, "replyText") {
				t.Fatalf("expected send_reply input replyText, got %#v", spec.InputSchema)
			}
		}
	}
	if !startFound || !sendReplyFound {
		t.Fatalf("expected start and send_reply specs in response")
	}
}

func TestBuildAIWorkflowNodeSpecsIncludesConditionValueOptions(t *testing.T) {
	specs := BuildAIWorkflowNodeSpecs(workflowregistry.DefaultRegistry().List())

	var action *workflowregistry.VariableSpec
	for _, spec := range specs {
		if spec.Type != workflowregistry.NodeTypeReplyPolicy {
			continue
		}
		for index := range spec.OutputSchema {
			if spec.OutputSchema[index].Name == "action" {
				action = &spec.OutputSchema[index]
				break
			}
		}
	}

	if action == nil {
		t.Fatalf("expected reply_policy action output")
	}
	if action.Label != "处理策略" {
		t.Fatalf("expected user-facing action label, got %q", action.Label)
	}
	if !hasResponseVariableOption(action.ValueOptions, "direct_reply", "直接回复客户") {
		t.Fatalf("expected direct_reply option with business label, got %#v", action.ValueOptions)
	}
	if !hasResponseOperator(action.Operators, "eq") || !hasResponseOperator(action.Operators, "neq") {
		t.Fatalf("expected action to constrain condition operators, got %#v", action.Operators)
	}
}

func TestBuildAIWorkflowRunIncludesAuditDisplayFields(t *testing.T) {
	startedAt := time.Date(2026, 6, 23, 10, 0, 0, 0, time.UTC)
	endedAt := startedAt.Add(1500 * time.Millisecond)

	resp := BuildAIWorkflowRunWithContext(
		&models.AIWorkflowRun{
			ID:                9,
			WorkflowID:        11,
			WorkflowVersionID: 22,
			AIAgentID:         33,
			StartedAt:         startedAt,
			EndedAt:           &endedAt,
			Status:            1,
		},
		&models.AIWorkflow{Name: "售后会话流程"},
		&models.AIWorkflowVersion{Version: 3},
		&models.AIAgent{Name: "售后 Agent"},
	)

	if resp.WorkflowName != "售后会话流程" {
		t.Fatalf("expected workflow name, got %q", resp.WorkflowName)
	}
	if resp.WorkflowVersion != 3 {
		t.Fatalf("expected workflow version 3, got %d", resp.WorkflowVersion)
	}
	if resp.AIAgentName != "售后 Agent" {
		t.Fatalf("expected agent name, got %q", resp.AIAgentName)
	}
	if resp.DurationMS != 1500 {
		t.Fatalf("expected duration 1500ms, got %d", resp.DurationMS)
	}
}

func TestBuildAIWorkflowRunDetailIncludesPublishedDefinitionSnapshot(t *testing.T) {
	definition := dsl.Definition{
		SchemaVersion: dsl.SchemaVersion,
		Nodes: []dsl.Node{
			{ID: "start_1", Type: workflowregistry.NodeTypeStart, Data: dsl.NodeData{Title: "开始"}},
			{ID: "reply_1", Type: workflowregistry.NodeTypeLLMReply, Data: dsl.NodeData{Title: "运行时回复"}},
		},
		Edges: []dsl.Edge{{SourceNodeID: "start_1", TargetNodeID: "reply_1", SourcePortID: "edge_start_reply"}},
	}
	buf, err := json.Marshal(definition)
	if err != nil {
		t.Fatalf("marshal definition: %v", err)
	}

	resp := BuildAIWorkflowRunDetailWithContext(
		&models.AIWorkflowRun{ID: 9, WorkflowVersionID: 22, Status: 1, StartedAt: time.Now()},
		nil,
		&models.AIWorkflow{Name: "当前 Workflow 草稿不应参与审计图"},
		&models.AIWorkflowVersion{Version: 3, Definition: string(buf)},
		&models.AIAgent{Name: "售后 Agent"},
	)

	if resp.Definition.SchemaVersion != dsl.SchemaVersion {
		t.Fatalf("expected run detail definition from published version, got %#v", resp.Definition)
	}
	if len(resp.Definition.Nodes) != 2 || resp.Definition.Nodes[1].Data.Title != "运行时回复" {
		t.Fatalf("expected published definition nodes, got %#v", resp.Definition.Nodes)
	}
	if len(resp.Definition.Edges) != 1 || resp.Definition.Edges[0].SourcePortID != "edge_start_reply" {
		t.Fatalf("expected published definition edges, got %#v", resp.Definition.Edges)
	}
}

func hasResponseVariable(items []workflowregistry.VariableSpec, name string) bool {
	for _, item := range items {
		if item.Name == name {
			return true
		}
	}
	return false
}

func hasResponseVariableOption(items []workflowregistry.VariableValueOption, value any, label string) bool {
	for _, item := range items {
		if item.Value == value && item.Label == label {
			return true
		}
	}
	return false
}

func hasResponseOperator(items []string, value string) bool {
	for _, item := range items {
		if item == value {
			return true
		}
	}
	return false
}
