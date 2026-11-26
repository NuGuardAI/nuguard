package dsl_test

import (
	"encoding/json"
	"strings"
	"testing"

	"agent-desk/internal/ai/workflow/dsl"
)

func TestDefinitionUnmarshalsFlowGramStyleSchema(t *testing.T) {
	raw := []byte(`{
		"schemaVersion": 2,
		"nodes": [{
			"id": "send_1",
			"type": "send_reply",
			"meta": {
				"position": { "x": 360, "y": 120 }
			},
			"data": {
				"title": "发送回复",
				"config": { "text": "hello" },
				"inputs": {
					"type": "object",
					"properties": {
						"replyText": { "type": "string" }
					},
					"required": ["replyText"]
				},
				"outputs": {
					"type": "object",
					"properties": {
						"sent": { "type": "boolean" }
					}
				},
				"inputsValues": {
					"replyText": {
						"type": "ref",
						"content": ["start_1", "userMessage"]
					}
				}
			}
		}],
		"edges": [{
			"sourceNodeID": "start_1",
			"targetNodeID": "send_1",
			"sourcePortID": "default"
		}]
	}`)

	var def dsl.Definition
	if err := json.Unmarshal(raw, &def); err != nil {
		t.Fatalf("unmarshal definition: %v", err)
	}

	if def.SchemaVersion != 2 {
		t.Fatalf("unexpected schema version: %d", def.SchemaVersion)
	}
	node := def.Nodes[0]
	if node.ID != "send_1" || node.Type != "send_reply" {
		t.Fatalf("unexpected node identity: %#v", node)
	}
	if node.Meta.Position.X != 360 || node.Meta.Position.Y != 120 {
		t.Fatalf("unexpected node position: %#v", node.Meta.Position)
	}
	if node.Data.Title != "发送回复" {
		t.Fatalf("unexpected node title: %q", node.Data.Title)
	}
	var config map[string]string
	if err := json.Unmarshal(node.Data.Config, &config); err != nil {
		t.Fatalf("unmarshal config: %v", err)
	}
	if config["text"] != "hello" {
		t.Fatalf("unexpected config: %s", node.Data.Config)
	}
	replyText := node.Data.InputsValues["replyText"]
	if replyText.Type != dsl.ValueTypeRef || len(replyText.Content) != 2 || replyText.Content[0] != "start_1" || replyText.Content[1] != "userMessage" {
		t.Fatalf("unexpected replyText value: %#v", replyText)
	}
	edge := def.Edges[0]
	if edge.SourceNodeID != "start_1" || edge.TargetNodeID != "send_1" || edge.SourcePortID != "default" {
		t.Fatalf("unexpected edge: %#v", edge)
	}
}

func TestDefinitionPreservesCanvasAnnotations(t *testing.T) {
	var def dsl.Definition
	err := json.Unmarshal([]byte(`{
		"schemaVersion": 2,
		"nodes": [],
		"annotations": [{
			"id": "comment_1",
			"type": "comment",
			"meta": {"position": {"x": 12, "y": 34}},
			"data": {"note": "check this branch", "size": {"width": 240, "height": 150}}
		}],
		"edges": []
	}`), &def)
	if err != nil {
		t.Fatalf("unmarshal definition: %v", err)
	}
	if len(def.Annotations) != 1 || def.Annotations[0].ID != "comment_1" {
		t.Fatalf("expected annotation to be preserved, got %#v", def.Annotations)
	}
	encoded, err := json.Marshal(def)
	if err != nil {
		t.Fatalf("marshal definition: %v", err)
	}
	if !strings.Contains(string(encoded), `"annotations"`) ||
		!strings.Contains(string(encoded), `"check this branch"`) {
		t.Fatalf("expected annotation JSON to round trip, got %s", encoded)
	}
}
