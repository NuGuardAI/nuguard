package validator_test

import (
	"encoding/json"
	"strings"
	"testing"

	"agent-desk/internal/ai/workflow/dsl"
	"agent-desk/internal/ai/workflow/registry"
	"agent-desk/internal/ai/workflow/validator"
)

func TestValidateDefinitionAcceptsMinimalFlowGramStyleFlow(t *testing.T) {
	result := validator.ValidateDefinition(minimalDefinition(), registry.DefaultRegistry())

	if !result.Valid {
		t.Fatalf("expected valid definition, got errors: %#v", result.Errors)
	}
}

func TestValidateDefinitionRejectsNodeMissingFromServerRuntime(t *testing.T) {
	def := dsl.Definition{
		Nodes: []dsl.Node{
			node("start_1", "start", nil, nil),
			node("http_1", "http", nil, nil),
			node("end_1", "end", nil, nil),
		},
		Edges: []dsl.Edge{edge("start_1", "http_1"), edge("http_1", "end_1")},
	}

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if result.Valid || !hasValidationMessage(result, "not supported by the server runtime") {
		t.Fatalf("expected unsupported-runtime error, got %#v", result.Errors)
	}
}

func TestValidateDefinitionAcceptsOfficialFlowGramCondition(t *testing.T) {
	def := dsl.Definition{
		Nodes: []dsl.Node{
			node("start_0", "start", nil, nil),
			{
				ID:   "condition_0",
				Type: "condition",
				Data: dsl.NodeData{
					Title: "Condition",
					Extra: map[string]json.RawMessage{
						"conditions": mustJSON([]dsl.FlowGramConditionItem{
							{
								Key: "if_0",
								Value: dsl.FlowGramCondition{
									Left:     dsl.RefValue("start_0", "query"),
									Operator: "contains",
									Right:    dsl.ConstantValue("hello"),
								},
							},
						}),
					},
				},
			},
			node("matched_end", "end", nil, nil),
			node("else_end", "end", nil, nil),
		},
		Edges: []dsl.Edge{
			edge("start_0", "condition_0"),
			portEdge("condition_0", "matched_end", "if_0"),
			portEdge("condition_0", "else_end", "else"),
		},
	}

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if !result.Valid {
		t.Fatalf("expected official FlowGram condition to be valid, got %#v", result.Errors)
	}
}

func TestValidateDefinitionRejectsMissingStart(t *testing.T) {
	def := minimalDefinition()
	def.Nodes = []dsl.Node{
		node("reply_1", "send_reply", inputs("replyText", dsl.RefValue("start_1", "userMessage")), nil),
		node("end_1", "end", nil, nil),
	}

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if result.Valid {
		t.Fatalf("expected missing start to be invalid")
	}
	if !hasValidationMessage(result, "exactly one start node") {
		t.Fatalf("expected start error, got %#v", result.Errors)
	}
}

func TestValidateDefinitionRejectsMissingRequiredInputValue(t *testing.T) {
	def := minimalDefinition()
	def.Nodes[1].Data.InputsValues = nil

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if result.Valid {
		t.Fatalf("expected missing required input mapping to be invalid")
	}
	if !hasValidationMessage(result, "required input mapping is missing") {
		t.Fatalf("expected required-input error, got %#v", result.Errors)
	}
}

func TestValidateDefinitionRejectsConstantConfirmationForHighRiskNode(t *testing.T) {
	def := dsl.Definition{
		Nodes: []dsl.Node{
			node("start_1", "start", nil, nil),
			node("confirm_1", "human_confirm", inputs("prompt", dsl.ConstantValue("请确认")), nil),
			node("create_1", "create_ticket", map[string]dsl.Value{
				"ticketDraft": dsl.ConstantValue(map[string]any{"title": "测试", "description": "测试描述"}),
				"confirmed":   dsl.ConstantValue(false),
			}, nil),
			node("end_1", "end", nil, nil),
		},
		Edges: []dsl.Edge{
			edge("start_1", "confirm_1"),
			edge("confirm_1", "create_1"),
			edge("create_1", "end_1"),
		},
	}

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if result.Valid || !hasValidationMessage(result, "confirmed input must come from human_confirm.confirmed") {
		t.Fatalf("expected confirmation-source error, got %#v", result.Errors)
	}
}

func TestValidateDefinitionRejectsUnknownInputSourceNode(t *testing.T) {
	def := minimalDefinition()
	def.Nodes[1].Data.InputsValues["replyText"] = dsl.RefValue("missing_1", "replyText")

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if result.Valid {
		t.Fatalf("expected unknown input source node to be invalid")
	}
	if !hasValidationMessage(result, "input source node does not exist") {
		t.Fatalf("expected source-node error, got %#v", result.Errors)
	}
}

func TestValidateDefinitionRejectsUnavailableInputSourceNode(t *testing.T) {
	def := minimalDefinition()
	def.Nodes = append(def.Nodes, node("late_1", "llm_reply", inputs("userMessage", dsl.RefValue("reply_1", "sent")), nil))
	def.Edges = append(def.Edges, edge("reply_1", "late_1"))
	def.Nodes[1].Data.InputsValues["replyText"] = dsl.RefValue("late_1", "replyText")

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if result.Valid {
		t.Fatalf("expected downstream input source to be invalid")
	}
	if !hasValidationMessage(result, "input source node is not available before current node") {
		t.Fatalf("expected source availability error, got %#v", result.Errors)
	}
}

func TestValidateDefinitionRejectsUnknownInputSourceField(t *testing.T) {
	def := minimalDefinition()
	def.Nodes[1].Data.InputsValues["replyText"] = dsl.RefValue("start_1", "missing")

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if result.Valid {
		t.Fatalf("expected unknown input source field to be invalid")
	}
	if !hasValidationMessage(result, "input source field does not exist") {
		t.Fatalf("expected source-field error, got %#v", result.Errors)
	}
}

func TestValidateDefinitionRejectsIncompatibleInputType(t *testing.T) {
	def := minimalDefinition()
	def.Nodes[1].Data.InputsValues["replyText"] = dsl.RefValue("start_1", "conversationId")

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if result.Valid {
		t.Fatalf("expected incompatible input type to be invalid")
	}
	if !hasValidationMessage(result, "input type mismatch") {
		t.Fatalf("expected type-mismatch error, got %#v", result.Errors)
	}
}

func TestValidateDefinitionAcceptsConfirmedCreateTicket(t *testing.T) {
	def := dsl.Definition{
		SchemaVersion: dsl.SchemaVersion,
		Nodes: []dsl.Node{
			node("start_1", "start", nil, nil),
			node("draft_1", "prepare_ticket_draft", inputs("issue", dsl.RefValue("start_1", "userMessage")), nil),
			node("confirm_1", "human_confirm", inputs("prompt", dsl.RefValue("start_1", "userMessage")), nil),
			node("create_1", "create_ticket", map[string]dsl.Value{
				"ticketDraft": dsl.RefValue("draft_1", "ticketDraft"),
				"confirmed":   dsl.RefValue("confirm_1", "confirmed"),
			}, nil),
			node("end_1", "end", nil, nil),
		},
		Edges: []dsl.Edge{
			edge("start_1", "draft_1"),
			edge("draft_1", "confirm_1"),
			edge("confirm_1", "create_1"),
			edge("create_1", "end_1"),
		},
	}

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if !result.Valid {
		t.Fatalf("expected confirmed create_ticket to be valid, got %#v", result.Errors)
	}
}

func TestValidateDefinitionRejectsConfirmedInputFromNonConfirmNode(t *testing.T) {
	def := minimalDefinition()
	def.Nodes = []dsl.Node{
		node("start_1", "start", nil, nil),
		node("draft_1", "prepare_ticket_draft", inputs("issue", dsl.RefValue("start_1", "userMessage")), nil),
		node("create_1", "create_ticket", map[string]dsl.Value{
			"ticketDraft": dsl.RefValue("draft_1", "ticketDraft"),
			"confirmed":   dsl.RefValue("start_1", "userMessage"),
		}, nil),
		node("end_1", "end", nil, nil),
	}
	def.Edges = []dsl.Edge{
		edge("start_1", "draft_1"),
		edge("draft_1", "create_1"),
		edge("create_1", "end_1"),
	}

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if result.Valid {
		t.Fatalf("expected confirmed input from non-confirm node to be invalid")
	}
	if !hasValidationMessage(result, "confirmed input must come from human_confirm.confirmed") {
		t.Fatalf("expected confirmed-source error, got %#v", result.Errors)
	}
}

func TestValidateDefinitionRejectsHandoffWithoutConfirmedInput(t *testing.T) {
	def := dsl.Definition{
		SchemaVersion: dsl.SchemaVersion,
		Nodes: []dsl.Node{
			node("start_1", "start", nil, nil),
			node("confirm_1", "human_confirm", inputs("prompt", dsl.RefValue("start_1", "userMessage")), nil),
			node("handoff_1", "handoff_to_human", inputs("reason", dsl.RefValue("start_1", "userMessage")), nil),
			node("end_1", "end", nil, nil),
		},
		Edges: []dsl.Edge{
			edge("start_1", "confirm_1"),
			edge("confirm_1", "handoff_1"),
			edge("handoff_1", "end_1"),
		},
	}

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if result.Valid {
		t.Fatalf("expected handoff without confirmed input to be invalid")
	}
	if !hasValidationMessage(result, "required input mapping is missing: confirmed") {
		t.Fatalf("expected missing confirmed input error, got %#v", result.Errors)
	}
}

func TestValidateDefinitionRejectsConditionBranchTargetWithoutEdge(t *testing.T) {
	def := conditionDefinition()
	def.Edges = []dsl.Edge{edge("start_1", "condition_1")}

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if result.Valid {
		t.Fatalf("expected condition branch target without edge to be invalid")
	}
	if !hasValidationMessage(result, "condition branch target must have an outgoing edge") {
		t.Fatalf("expected branch edge error, got %#v", result.Errors)
	}
}

func TestValidateDefinitionRejectsConditionBranchTargetWithoutPortEdge(t *testing.T) {
	def := conditionDefinition()
	def.Edges = []dsl.Edge{
		edge("start_1", "condition_1"),
		edge("condition_1", "end_1"),
		portEdge("condition_1", "end_1", "default"),
	}

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if result.Valid {
		t.Fatalf("expected condition branch target without matching port edge to be invalid")
	}
	if !hasValidationMessage(result, "condition branch target must have an outgoing edge") {
		t.Fatalf("expected branch port edge error, got %#v", result.Errors)
	}
}

func TestValidateDefinitionRejectsUnknownConditionVariable(t *testing.T) {
	def := conditionDefinition()
	var config dsl.ConditionConfig
	if err := json.Unmarshal(def.Nodes[1].Data.Config, &config); err != nil {
		t.Fatalf("unmarshal condition config: %v", err)
	}
	config.Branches[0].Condition.Left = &dsl.Value{Type: dsl.ValueTypeRef, Content: []string{"start_1", "missing"}}
	def.Nodes[1].Data.Config = mustJSON(config)

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if result.Valid {
		t.Fatalf("expected unknown condition variable to be invalid")
	}
	if !hasValidationMessage(result, "condition source field does not exist") {
		t.Fatalf("expected condition variable error, got %#v", result.Errors)
	}
}

func TestValidateDefinitionRejectsKnowledgeRetrieveWithoutKnowledgeBases(t *testing.T) {
	def := knowledgeRetrieveDefinition(nil)

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if result.Valid {
		t.Fatalf("expected knowledge_retrieve without knowledge bases to be invalid")
	}
	if !hasValidationMessage(result, "需要选择至少一个知识库") {
		t.Fatalf("expected missing knowledge base error, got %#v", result.Errors)
	}
}

func TestValidateDefinitionRejectsKnowledgeRetrieveWithInvalidKnowledgeBaseID(t *testing.T) {
	def := knowledgeRetrieveDefinition(map[string]any{"knowledgeBaseIds": []int64{0, -1}})

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if result.Valid {
		t.Fatalf("expected invalid knowledge base id to be invalid")
	}
	if !hasValidationMessage(result, "知识库 ID 必须大于 0") {
		t.Fatalf("expected invalid knowledge base id error, got %#v", result.Errors)
	}
}

func TestValidateDefinitionAcceptsKnowledgeRetrieveWithKnowledgeBases(t *testing.T) {
	def := knowledgeRetrieveDefinition(map[string]any{"knowledgeBaseIds": []int64{1, 2}})

	result := validator.ValidateDefinition(def, registry.DefaultRegistry())

	if !result.Valid {
		t.Fatalf("expected knowledge_retrieve with knowledge bases to be valid, got %#v", result.Errors)
	}
}

func minimalDefinition() dsl.Definition {
	return dsl.Definition{
		SchemaVersion: dsl.SchemaVersion,
		Nodes: []dsl.Node{
			node("start_1", "start", nil, nil),
			node("reply_1", "send_reply", inputs("replyText", dsl.RefValue("start_1", "userMessage")), map[string]any{"text": "hello"}),
			node("end_1", "end", nil, nil),
		},
		Edges: []dsl.Edge{
			edge("start_1", "reply_1"),
			edge("reply_1", "end_1"),
		},
	}
}

func knowledgeRetrieveDefinition(config any) dsl.Definition {
	return dsl.Definition{
		SchemaVersion: dsl.SchemaVersion,
		Nodes: []dsl.Node{
			node("start_1", "start", nil, nil),
			node("retrieve_1", "knowledge_retrieve", inputs("query", dsl.RefValue("start_1", "userMessage")), config),
			node("end_1", "end", nil, nil),
		},
		Edges: []dsl.Edge{
			edge("start_1", "retrieve_1"),
			edge("retrieve_1", "end_1"),
		},
	}
}

func conditionDefinition() dsl.Definition {
	conditionConfig := dsl.ConditionConfig{
		Branches: []dsl.ConditionBranch{
			{
				ID:           "hello",
				Name:         "Hello",
				TargetNodeID: "end_1",
				Condition: &dsl.Condition{
					Left:     &dsl.Value{Type: dsl.ValueTypeRef, Content: []string{"start_1", "userMessage"}},
					Operator: "eq",
					Right:    "hello",
				},
			},
			{
				ID:           "default",
				Name:         "Default",
				TargetNodeID: "end_1",
				Default:      true,
			},
		},
	}
	return dsl.Definition{
		SchemaVersion: dsl.SchemaVersion,
		Nodes: []dsl.Node{
			node("start_1", "start", nil, nil),
			node("condition_1", "condition", nil, conditionConfig),
			node("end_1", "end", nil, nil),
		},
		Edges: []dsl.Edge{
			edge("start_1", "condition_1"),
			portEdge("condition_1", "end_1", "hello"),
			portEdge("condition_1", "end_1", "default"),
		},
	}
}

func node(id string, nodeType string, inputValues map[string]dsl.Value, config any) dsl.Node {
	return dsl.Node{
		ID:   id,
		Type: nodeType,
		Meta: dsl.NodeMeta{Position: dsl.Position{X: 0, Y: 0}},
		Data: dsl.NodeData{
			Title:        nodeType,
			Config:       mustJSON(config),
			InputsValues: inputValues,
		},
	}
}

func edge(source string, target string) dsl.Edge {
	return dsl.Edge{SourceNodeID: source, TargetNodeID: target}
}

func portEdge(source string, target string, sourcePortID string) dsl.Edge {
	return dsl.Edge{SourceNodeID: source, TargetNodeID: target, SourcePortID: sourcePortID}
}

func inputs(name string, value dsl.Value) map[string]dsl.Value {
	return map[string]dsl.Value{name: value}
}

func mustJSON(value any) json.RawMessage {
	if value == nil {
		return nil
	}
	raw, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	return raw
}

func hasValidationMessage(result validator.Result, want string) bool {
	for _, item := range result.Errors {
		if strings.Contains(item.Message, want) {
			return true
		}
	}
	return false
}
