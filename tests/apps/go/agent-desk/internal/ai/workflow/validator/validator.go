package validator

import (
	"encoding/json"
	"fmt"
	"strings"

	"agent-desk/internal/ai/workflow/dsl"
	"agent-desk/internal/ai/workflow/registry"
)

type Error struct {
	Field   string `json:"field"`
	Message string `json:"message"`
}

type Result struct {
	Valid  bool    `json:"valid"`
	Errors []Error `json:"errors"`
}

func ValidateDefinition(def dsl.Definition, reg *registry.Registry) Result {
	if reg == nil {
		reg = registry.DefaultRegistry()
	}
	v := definitionValidator{
		def:          def,
		registry:     reg,
		nodesByID:    make(map[string]dsl.Node, len(def.Nodes)),
		outgoing:     make(map[string][]string),
		incoming:     make(map[string][]string),
		startNodeIDs: make([]string, 0, 1),
		endNodeIDs:   make([]string, 0, 1),
	}
	v.validate()
	return Result{
		Valid:  len(v.errors) == 0,
		Errors: v.errors,
	}
}

type definitionValidator struct {
	def          dsl.Definition
	registry     *registry.Registry
	nodesByID    map[string]dsl.Node
	outgoing     map[string][]string
	incoming     map[string][]string
	startNodeIDs []string
	endNodeIDs   []string
	errors       []Error
}

func (v *definitionValidator) validate() {
	v.validateNodes()
	v.validateEdges()
	v.validateKnowledgeRetrieveConfigs()
	v.validateReachability()
	v.validateConfirmationGuards()
	v.validateVariableMappings()
	v.validateConditions()
}

func (v *definitionValidator) validateNodes() {
	for index, node := range v.def.Nodes {
		node.ID = strings.TrimSpace(node.ID)
		node.Type = strings.TrimSpace(node.Type)
		field := fmt.Sprintf("nodes[%d]", index)
		if node.ID == "" {
			v.addError(field+".id", "node id is required")
			continue
		}
		if _, exists := v.nodesByID[node.ID]; exists {
			v.addError(field+".id", "duplicate node id: "+node.ID)
			continue
		}
		v.nodesByID[node.ID] = node
		if node.Type == "" {
			v.addError(field+".type", "node type is required")
			continue
		}
		if _, ok := v.registry.Get(node.Type); !ok {
			v.addError(field+".type", "unknown node type: "+node.Type)
			continue
		}
		if !registry.IsExecutableNodeType(node.Type) {
			v.addError(field+".type", "node type is not supported by the server runtime: "+node.Type)
			continue
		}
		switch node.Type {
		case registry.NodeTypeStart:
			v.startNodeIDs = append(v.startNodeIDs, node.ID)
		case registry.NodeTypeEnd:
			v.endNodeIDs = append(v.endNodeIDs, node.ID)
		}
	}
	if len(v.startNodeIDs) != 1 {
		v.addError("nodes", "workflow must contain exactly one start node")
	}
	if len(v.endNodeIDs) == 0 {
		v.addError("nodes", "workflow must contain at least one end node")
	}
}

func (v *definitionValidator) validateEdges() {
	for index, edge := range v.def.Edges {
		source := strings.TrimSpace(edge.SourceNodeID)
		target := strings.TrimSpace(edge.TargetNodeID)
		field := fmt.Sprintf("edges[%d]", index)
		if source == "" {
			v.addError(field+".sourceNodeID", "edge source node is required")
		} else if _, ok := v.nodesByID[source]; !ok {
			v.addError(field+".sourceNodeID", "edge source node does not exist: "+source)
		}
		if target == "" {
			v.addError(field+".targetNodeID", "edge target node is required")
		} else if _, ok := v.nodesByID[target]; !ok {
			v.addError(field+".targetNodeID", "edge target node does not exist: "+target)
		}
		if source != "" && target != "" {
			v.outgoing[source] = append(v.outgoing[source], target)
			v.incoming[target] = append(v.incoming[target], source)
		}
	}
}

func (v *definitionValidator) validateReachability() {
	entryNodeID := v.entryNodeID()
	if entryNodeID == "" {
		return
	}
	if _, ok := v.nodesByID[entryNodeID]; !ok {
		return
	}
	reachable := make(map[string]struct{}, len(v.nodesByID))
	queue := []string{entryNodeID}
	for len(queue) > 0 {
		current := queue[0]
		queue = queue[1:]
		if _, exists := reachable[current]; exists {
			continue
		}
		reachable[current] = struct{}{}
		for _, target := range v.outgoing[current] {
			if _, exists := reachable[target]; !exists {
				queue = append(queue, target)
			}
		}
	}
	for id := range v.nodesByID {
		if _, ok := reachable[id]; !ok {
			v.addError("nodes", "node is not reachable from entry node: "+id)
		}
	}
}

func (v *definitionValidator) validateConfirmationGuards() {
	for id, node := range v.nodesByID {
		spec, ok := v.registry.Get(node.Type)
		if !ok || !spec.RequiresConfirmationPredecessor {
			continue
		}
		if !v.hasConfirmationPredecessor(id, make(map[string]struct{})) {
			v.addError("nodes."+id, node.Type+" requires human_confirm before execution")
		}
		v.validateConfirmedInput(id, node)
	}
}

func (v *definitionValidator) validateConfirmedInput(nodeID string, node dsl.Node) {
	value, ok := node.Data.InputsValues["confirmed"]
	sourceNodeID, sourceField, refOK := value.Ref()
	field := "nodes." + nodeID + ".data.inputsValues.confirmed"
	if !ok || !refOK || strings.TrimSpace(sourceNodeID) == "" || strings.TrimSpace(sourceField) == "" {
		v.addError(field, "confirmed input must come from human_confirm.confirmed")
		return
	}
	sourceNode, ok := v.nodesByID[sourceNodeID]
	if !ok {
		return
	}
	if sourceNode.Type != registry.NodeTypeHumanConfirm || strings.TrimSpace(sourceField) != "confirmed" {
		v.addError(field, "confirmed input must come from human_confirm.confirmed")
	}
}

func (v *definitionValidator) validateVariableMappings() {
	for id, node := range v.nodesByID {
		spec, ok := v.registry.Get(node.Type)
		if !ok {
			continue
		}
		for _, input := range spec.InputSchema {
			if !input.Required {
				continue
			}
			value, ok := node.Data.InputsValues[input.Name]
			if !ok {
				v.addError("nodes."+id+".data.inputsValues."+input.Name, "required input mapping is missing: "+input.Name)
				continue
			}
			v.validateInputValue(id, input, value)
		}
		for inputName, value := range node.Data.InputsValues {
			if _, ok := findInputSpec(spec.InputSchema, inputName); ok {
				continue
			}
			sourceNodeID, sourceField, refOK := value.Ref()
			if !refOK {
				continue
			}
			sourceNode, sourceOK := v.nodesByID[strings.TrimSpace(sourceNodeID)]
			if !sourceOK {
				v.addError("nodes."+id+".data.inputsValues."+inputName, "input source node does not exist: "+sourceNodeID)
				continue
			}
			sourceSpec, sourceSpecOK := v.registry.Get(sourceNode.Type)
			if !sourceSpecOK {
				continue
			}
			if _, ok := findOutputSpec(sourceSpec.OutputSchema, sourceField); !ok {
				v.addError("nodes."+id+".data.inputsValues."+inputName, "input source field does not exist: "+sourceNodeID+"."+sourceField)
			}
		}
	}
}

func (v *definitionValidator) validateInputValue(nodeID string, input registry.VariableSpec, value dsl.Value) {
	sourceNodeID, sourceField, ok := value.Ref()
	if !ok {
		if value.Type == dsl.ValueTypeConstant || value.Type == dsl.ValueTypeTemplate {
			return
		}
		v.addError("nodes."+nodeID+".data.inputsValues."+input.Name, "input mapping source is required")
		return
	}
	sourceNodeID = strings.TrimSpace(sourceNodeID)
	sourceField = strings.TrimSpace(sourceField)
	sourceNode, ok := v.nodesByID[sourceNodeID]
	if !ok {
		v.addError("nodes."+nodeID+".data.inputsValues."+input.Name, "input source node does not exist: "+sourceNodeID)
		return
	}
	if !v.hasPath(sourceNodeID, nodeID, make(map[string]struct{})) {
		v.addError("nodes."+nodeID+".data.inputsValues."+input.Name, "input source node is not available before current node: "+sourceNodeID)
		return
	}
	sourceSpec, ok := v.registry.Get(sourceNode.Type)
	if !ok {
		return
	}
	output, ok := findOutputSpec(sourceSpec.OutputSchema, sourceField)
	if !ok {
		v.addError("nodes."+nodeID+".data.inputsValues."+input.Name, "input source field does not exist: "+sourceNodeID+"."+sourceField)
		return
	}
	if !variableTypesCompatible(input.Type, output.Type) {
		v.addError("nodes."+nodeID+".data.inputsValues."+input.Name, fmt.Sprintf("input type mismatch: %s expects %s but %s.%s is %s", input.Name, input.Type, sourceNodeID, sourceField, output.Type))
	}
}

func (v *definitionValidator) validateConditions() {
	for index, node := range v.def.Nodes {
		if strings.TrimSpace(node.Type) != registry.NodeTypeCondition {
			continue
		}
		if rawConditions, ok := node.Data.Extra["conditions"]; ok {
			v.validateFlowGramConditions(index, node, rawConditions)
			continue
		}
		field := fmt.Sprintf("nodes[%d].config.branches", index)
		config := dsl.ConditionConfig{}
		if len(node.Data.Config) > 0 {
			if err := json.Unmarshal(node.Data.Config, &config); err != nil {
				v.addError(field, "condition branches config must be valid JSON")
				continue
			}
		}
		if len(config.Branches) == 0 {
			v.addError(field, "condition node must include at least one branch")
			continue
		}
		defaultCount := 0
		seenBranchIDs := make(map[string]struct{}, len(config.Branches))
		for branchIndex, branch := range config.Branches {
			branchField := fmt.Sprintf("%s[%d]", field, branchIndex)
			branchID := strings.TrimSpace(branch.ID)
			if branchID == "" {
				v.addError(branchField+".id", "condition branch id is required")
			} else if _, exists := seenBranchIDs[branchID]; exists {
				v.addError(branchField+".id", "duplicate condition branch id: "+branchID)
			}
			seenBranchIDs[branchID] = struct{}{}
			targetNodeID := strings.TrimSpace(branch.TargetNodeID)
			if targetNodeID == "" {
				v.addError(branchField+".targetNodeId", "condition branch target node is required")
			} else if _, ok := v.nodesByID[targetNodeID]; !ok {
				v.addError(branchField+".targetNodeId", "condition branch target node does not exist: "+targetNodeID)
			}
			if !v.hasConditionBranchEdge(strings.TrimSpace(node.ID), targetNodeID, branchID) {
				v.addError(branchField+".targetNodeId", "condition branch target must have an outgoing edge: "+targetNodeID)
			}
			if branch.Default {
				defaultCount++
				if branch.Condition != nil {
					v.addError(branchField+".condition", "default condition branch must not define a condition")
				}
				if branchIndex != len(config.Branches)-1 {
					v.addError(branchField, "default condition branch must be last")
				}
				continue
			}
			v.validateCondition(branchField+".condition", strings.TrimSpace(node.ID), branch.Condition)
		}
		if defaultCount != 1 {
			v.addError(field, "condition node must include exactly one default branch")
		}
	}
}

func (v *definitionValidator) validateFlowGramConditions(index int, node dsl.Node, raw json.RawMessage) {
	field := fmt.Sprintf("nodes[%d].data.conditions", index)
	var conditions []dsl.FlowGramConditionItem
	if err := json.Unmarshal(raw, &conditions); err != nil {
		v.addError(field, "condition data must be valid JSON")
		return
	}
	if len(conditions) == 0 {
		v.addError(field, "condition node must include at least one condition")
		return
	}
	seenKeys := make(map[string]struct{}, len(conditions))
	for conditionIndex, item := range conditions {
		itemField := fmt.Sprintf("%s[%d]", field, conditionIndex)
		key := strings.TrimSpace(item.Key)
		if key == "" {
			v.addError(itemField+".key", "condition key is required")
		} else if _, exists := seenKeys[key]; exists {
			v.addError(itemField+".key", "duplicate condition key: "+key)
		}
		seenKeys[key] = struct{}{}
		if !v.hasConditionPortEdge(strings.TrimSpace(node.ID), key) {
			v.addError(itemField+".key", "condition output port must have an outgoing edge: "+key)
		}
		v.validateFlowGramCondition(itemField+".value", strings.TrimSpace(node.ID), item.Value)
	}
	if !v.hasConditionPortEdge(strings.TrimSpace(node.ID), "else") {
		v.addError(field, "condition else port must have an outgoing edge")
	}
}

func (v *definitionValidator) validateFlowGramCondition(field string, sourceNodeID string, condition dsl.FlowGramCondition) {
	operator := strings.TrimSpace(condition.Operator)
	if !isSupportedConditionOperator(operator) {
		v.addError(field+".operator", "unsupported condition operator: "+operator)
		return
	}
	sourceSelectorNodeID, sourceField, ok := condition.Left.Ref()
	sourceSelectorNodeID = strings.TrimSpace(sourceSelectorNodeID)
	sourceField = strings.TrimSpace(sourceField)
	if !ok || sourceSelectorNodeID == "" || sourceField == "" {
		v.addError(field+".left", "condition left variable is required")
		return
	}
	if _, exists := v.nodesByID[sourceSelectorNodeID]; !exists {
		v.addError(field+".left", "condition source node does not exist: "+sourceSelectorNodeID)
		return
	}
	if sourceNodeID != "" && !v.hasPath(sourceSelectorNodeID, sourceNodeID, make(map[string]struct{})) && sourceSelectorNodeID != sourceNodeID {
		v.addError(field+".left", "condition source node is not available before branch: "+sourceSelectorNodeID)
	}
	if !conditionOperatorWithoutRight(operator) && condition.Right.Type == "" {
		v.addError(field+".right", "condition comparison value is required")
	}
}

func (v *definitionValidator) hasConditionPortEdge(sourceID string, sourcePortID string) bool {
	for _, edge := range v.def.Edges {
		if strings.TrimSpace(edge.SourceNodeID) == sourceID &&
			strings.TrimSpace(edge.SourcePortID) == sourcePortID {
			return true
		}
	}
	return false
}

func (v *definitionValidator) validateKnowledgeRetrieveConfigs() {
	for index, node := range v.def.Nodes {
		if strings.TrimSpace(node.Type) != registry.NodeTypeKnowledgeRetrieve {
			continue
		}
		field := fmt.Sprintf("nodes[%d].config.knowledgeBaseIds", index)
		ids, ok := readKnowledgeBaseIDsFromConfig(node.Data.Config)
		if !ok || len(ids) == 0 {
			v.addError(field, "知识检索节点需要选择至少一个知识库")
			continue
		}
		for _, id := range ids {
			if id <= 0 {
				v.addError(field, "知识库 ID 必须大于 0")
				break
			}
		}
	}
}

func readKnowledgeBaseIDsFromConfig(raw json.RawMessage) ([]int64, bool) {
	if len(raw) == 0 {
		return nil, false
	}
	var cfg map[string]any
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return nil, false
	}
	rawIDs, ok := cfg["knowledgeBaseIds"]
	if !ok {
		return nil, false
	}
	values, ok := rawIDs.([]any)
	if !ok {
		return nil, false
	}
	ret := make([]int64, 0, len(values))
	for _, value := range values {
		switch v := value.(type) {
		case float64:
			ret = append(ret, int64(v))
		case int64:
			ret = append(ret, v)
		case int:
			ret = append(ret, int64(v))
		default:
			return nil, false
		}
	}
	return ret, true
}

func (v *definitionValidator) validateCondition(field string, sourceNodeID string, condition *dsl.Condition) {
	if condition == nil {
		v.addError(field, "condition branch condition is required")
		return
	}
	operator := strings.TrimSpace(condition.Operator)
	if operator == "" && strings.TrimSpace(condition.Expression) != "" {
		v.addError(field+".expression", "free-form condition expressions are not supported")
		return
	}
	if !isSupportedConditionOperator(operator) {
		v.addError(field+".operator", "unsupported condition operator: "+operator)
		return
	}
	if condition.Left == nil {
		v.addError(field+".left", "condition left variable is required")
		return
	}
	sourceSelectorNodeID, sourceField, leftOK := condition.Left.Ref()
	sourceSelectorNodeID = strings.TrimSpace(sourceSelectorNodeID)
	sourceField = strings.TrimSpace(sourceField)
	if !leftOK || sourceSelectorNodeID == "" || sourceField == "" {
		v.addError(field+".left", "condition left variable is required")
		return
	}
	sourceNode, ok := v.nodesByID[sourceSelectorNodeID]
	if !ok {
		v.addError(field+".left", "condition source node does not exist: "+sourceSelectorNodeID)
		return
	}
	if sourceNodeID != "" && !v.hasPath(sourceSelectorNodeID, sourceNodeID, make(map[string]struct{})) && sourceSelectorNodeID != sourceNodeID {
		v.addError(field+".left", "condition source node is not available before branch: "+sourceSelectorNodeID)
		return
	}
	sourceSpec, ok := v.registry.Get(sourceNode.Type)
	if !ok {
		return
	}
	outputSpec, ok := findOutputSpec(sourceSpec.OutputSchema, sourceField)
	if !ok {
		v.addError(field+".left", "condition source field does not exist: "+sourceSelectorNodeID+"."+sourceField)
		return
	}
	if len(outputSpec.Operators) > 0 && !stringInSlice(outputSpec.Operators, operator) {
		v.addError(field+".operator", "condition operator is not allowed for variable: "+operator)
		return
	}
	if !conditionOperatorWithoutRight(operator) && len(outputSpec.ValueOptions) > 0 && !valueOptionExists(outputSpec.ValueOptions, condition.Right) {
		v.addError(field+".right", "condition comparison value is not allowed")
	}
}

func isSupportedConditionOperator(operator string) bool {
	switch strings.TrimSpace(operator) {
	case "eq", "equals", "neq", "not_equals", "contains", "exists", "not_exists", "truthy", "is_true", "falsy", "is_false", "gt", "gte", "lt", "lte":
		return true
	default:
		return false
	}
}

func conditionOperatorWithoutRight(operator string) bool {
	switch strings.TrimSpace(operator) {
	case "exists", "not_exists", "truthy", "is_true", "falsy", "is_false":
		return true
	default:
		return false
	}
}

func stringInSlice(items []string, value string) bool {
	for _, item := range items {
		if strings.TrimSpace(item) == value {
			return true
		}
	}
	return false
}

func valueOptionExists(items []registry.VariableValueOption, value any) bool {
	for _, item := range items {
		if conditionValuesEqual(item.Value, value) {
			return true
		}
	}
	return false
}

func conditionValuesEqual(left any, right any) bool {
	switch l := left.(type) {
	case string:
		r, ok := right.(string)
		return ok && l == r
	case bool:
		r, ok := right.(bool)
		return ok && l == r
	case int:
		return conditionValuesEqual(float64(l), right)
	case int64:
		return conditionValuesEqual(float64(l), right)
	case float64:
		switch r := right.(type) {
		case int:
			return l == float64(r)
		case int64:
			return l == float64(r)
		case float64:
			return l == r
		default:
			return false
		}
	default:
		return false
	}
}

func (v *definitionValidator) hasPath(sourceID string, targetID string, visiting map[string]struct{}) bool {
	if sourceID == targetID {
		return false
	}
	if _, seen := visiting[sourceID]; seen {
		return false
	}
	visiting[sourceID] = struct{}{}
	for _, next := range v.outgoing[sourceID] {
		if next == targetID {
			return true
		}
		if v.hasPath(next, targetID, visiting) {
			return true
		}
	}
	return false
}

func (v *definitionValidator) hasConditionBranchEdge(sourceID string, targetID string, sourcePortID string) bool {
	if sourceID == "" || targetID == "" || sourcePortID == "" {
		return true
	}
	for _, edge := range v.def.Edges {
		if strings.TrimSpace(edge.SourceNodeID) == sourceID &&
			strings.TrimSpace(edge.TargetNodeID) == targetID &&
			strings.TrimSpace(edge.SourcePortID) == sourcePortID {
			return true
		}
	}
	return false
}

func (v *definitionValidator) entryNodeID() string {
	if len(v.startNodeIDs) != 1 {
		return ""
	}
	return v.startNodeIDs[0]
}

func findInputSpec(items []registry.VariableSpec, name string) (registry.VariableSpec, bool) {
	name = strings.TrimSpace(name)
	for _, item := range items {
		if item.Name == name {
			return item, true
		}
	}
	return registry.VariableSpec{}, false
}

func findOutputSpec(items []registry.VariableSpec, name string) (registry.VariableSpec, bool) {
	name = strings.TrimSpace(name)
	for _, item := range items {
		if item.Name == name {
			return item, true
		}
	}
	return registry.VariableSpec{}, false
}

func variableTypesCompatible(input registry.VariableType, output registry.VariableType) bool {
	return input == registry.VariableTypeAny || output == registry.VariableTypeAny || input == output
}

func (v *definitionValidator) hasConfirmationPredecessor(nodeID string, visiting map[string]struct{}) bool {
	if _, seen := visiting[nodeID]; seen {
		return false
	}
	visiting[nodeID] = struct{}{}
	for _, source := range v.incoming[nodeID] {
		node, ok := v.nodesByID[source]
		if !ok {
			continue
		}
		if node.Type == registry.NodeTypeHumanConfirm {
			return true
		}
		if v.hasConfirmationPredecessor(source, visiting) {
			return true
		}
	}
	return false
}

func (v *definitionValidator) addError(field string, message string) {
	v.errors = append(v.errors, Error{Field: field, Message: message})
}
