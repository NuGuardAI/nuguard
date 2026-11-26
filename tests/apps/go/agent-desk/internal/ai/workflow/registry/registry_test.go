package registry

import "testing"

func TestDefaultRegistryMarksOnlyRuntimeSupportedNodesExecutable(t *testing.T) {
	registry := DefaultRegistry()
	for _, nodeType := range []string{NodeTypeCreateTicket, NodeTypeHumanConfirm, NodeTypeSendReply, NodeTypeLLM} {
		spec, ok := registry.Get(nodeType)
		if !ok || !spec.Executable {
			t.Fatalf("expected %s to be executable, got %#v", nodeType, spec)
		}
	}
	for _, nodeType := range []string{NodeTypeHTTP, NodeTypeCode, NodeTypeLoop} {
		spec, ok := registry.Get(nodeType)
		if !ok || spec.Executable {
			t.Fatalf("expected %s to be unavailable in server runtime, got %#v", nodeType, spec)
		}
	}
}

func TestDefaultRegistryVariablesHaveBusinessLabels(t *testing.T) {
	for _, spec := range DefaultRegistry().List() {
		for _, variable := range append(spec.InputSchema, spec.OutputSchema...) {
			if variable.Label == "" {
				t.Fatalf("%s.%s is missing business label", spec.Type, variable.Name)
			}
			if variable.Description == "" || variable.Description == variable.Name {
				t.Fatalf("%s.%s is missing readable description", spec.Type, variable.Name)
			}
		}
	}
}

func TestDefaultRegistryExposesStartOutputs(t *testing.T) {
	spec, ok := DefaultRegistry().Get(NodeTypeStart)
	if !ok {
		t.Fatalf("start node spec not found")
	}
	if !hasVariable(spec.OutputSchema, "userMessage", VariableTypeString) {
		t.Fatalf("expected start output userMessage:string, got %#v", spec.OutputSchema)
	}
	if hasVariableName(spec.OutputSchema, "knowledgeBaseIds") {
		t.Fatalf("did not expect start output knowledgeBaseIds after knowledge binding moved to retrieve node, got %#v", spec.OutputSchema)
	}
}

func TestDefaultRegistryExposesKnowledgeRetrieveInputsAndOutputs(t *testing.T) {
	spec, ok := DefaultRegistry().Get(NodeTypeKnowledgeRetrieve)
	if !ok {
		t.Fatalf("knowledge_retrieve node spec not found")
	}
	if !hasRequiredVariable(spec.InputSchema, "query", VariableTypeString) {
		t.Fatalf("expected knowledge_retrieve required input query:string, got %#v", spec.InputSchema)
	}
	if !hasVariable(spec.OutputSchema, "items", VariableTypeObjectArray) {
		t.Fatalf("expected knowledge_retrieve output items:array<object>, got %#v", spec.OutputSchema)
	}
	if spec.ConfigSchema == nil {
		t.Fatalf("expected knowledge_retrieve config schema")
	}
}

func TestDefaultRegistryExposesSendReplyRequiredInput(t *testing.T) {
	spec, ok := DefaultRegistry().Get(NodeTypeSendReply)
	if !ok {
		t.Fatalf("send_reply node spec not found")
	}
	if !hasRequiredVariable(spec.InputSchema, "replyText", VariableTypeString) {
		t.Fatalf("expected send_reply required input replyText:string, got %#v", spec.InputSchema)
	}
	if !hasVariable(spec.OutputSchema, "sent", VariableTypeBoolean) {
		t.Fatalf("expected send_reply output sent:boolean, got %#v", spec.OutputSchema)
	}
}

func TestDefaultRegistryExposesConversationUnderstandingOutputs(t *testing.T) {
	spec, ok := DefaultRegistry().Get(NodeTypeConversationUnderstanding)
	if !ok {
		t.Fatalf("conversation_understanding node spec not found")
	}
	if !hasRequiredVariable(spec.InputSchema, "userMessage", VariableTypeString) {
		t.Fatalf("expected conversation_understanding required input userMessage:string, got %#v", spec.InputSchema)
	}
	for _, want := range []string{"messageIntent", "answerScope", "riskSignals", "reason"} {
		if !hasVariableName(spec.OutputSchema, want) {
			t.Fatalf("expected conversation_understanding output %s, got %#v", want, spec.OutputSchema)
		}
	}
	if !hasVariable(spec.OutputSchema, "confidence", VariableTypeNumber) {
		t.Fatalf("expected conversation_understanding output confidence:number, got %#v", spec.OutputSchema)
	}
}

func TestDefaultRegistryExposesReplyPolicyOutputs(t *testing.T) {
	spec, ok := DefaultRegistry().Get(NodeTypeReplyPolicy)
	if !ok {
		t.Fatalf("reply_policy node spec not found")
	}
	if !hasRequiredVariable(spec.InputSchema, "messageIntent", VariableTypeString) {
		t.Fatalf("expected reply_policy required input messageIntent:string, got %#v", spec.InputSchema)
	}
	if !hasRequiredVariable(spec.InputSchema, "answerScope", VariableTypeString) {
		t.Fatalf("expected reply_policy required input answerScope:string, got %#v", spec.InputSchema)
	}
	for _, want := range []string{"action", "replyText", "reason", "finalReplySource"} {
		if !hasVariableName(spec.OutputSchema, want) {
			t.Fatalf("expected reply_policy output %s, got %#v", want, spec.OutputSchema)
		}
	}
}

func hasRequiredVariable(items []VariableSpec, name string, variableType VariableType) bool {
	for _, item := range items {
		if item.Name == name && item.Type == variableType && item.Required {
			return true
		}
	}
	return false
}

func hasVariableName(items []VariableSpec, name string) bool {
	for _, item := range items {
		if item.Name == name {
			return true
		}
	}
	return false
}

func hasVariable(items []VariableSpec, name string, variableType VariableType) bool {
	for _, item := range items {
		if item.Name == name && item.Type == variableType {
			return true
		}
	}
	return false
}
