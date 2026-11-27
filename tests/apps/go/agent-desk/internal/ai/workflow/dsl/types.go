package dsl

import "encoding/json"

const SchemaVersion = 2

type Definition struct {
	SchemaVersion  int             `json:"schemaVersion,omitempty"`
	Nodes          []Node          `json:"nodes"`
	Annotations    []Node          `json:"annotations,omitempty"`
	Edges          []Edge          `json:"edges"`
	GlobalVariable json.RawMessage `json:"globalVariable,omitempty"`
}

type Node struct {
	ID     string   `json:"id"`
	Type   string   `json:"type"`
	Meta   NodeMeta `json:"meta"`
	Data   NodeData `json:"data"`
	Blocks []Node   `json:"blocks,omitempty"`
	Edges  []Edge   `json:"edges,omitempty"`
}

type NodeMeta struct {
	Position Position `json:"position"`
}

type NodeData struct {
	Title        string                     `json:"title,omitempty"`
	Config       json.RawMessage            `json:"config,omitempty"`
	Inputs       json.RawMessage            `json:"inputs,omitempty"`
	Outputs      json.RawMessage            `json:"outputs,omitempty"`
	InputsValues map[string]Value           `json:"inputsValues,omitempty"`
	Extra        map[string]json.RawMessage `json:"-"`
}

type Position struct {
	X float64 `json:"x"`
	Y float64 `json:"y"`
}

type Edge struct {
	SourceNodeID string `json:"sourceNodeID"`
	TargetNodeID string `json:"targetNodeID"`
	SourcePortID string `json:"sourcePortID,omitempty"`
	TargetPortID string `json:"targetPortID,omitempty"`
}

type ValueType string

const (
	ValueTypeConstant ValueType = "constant"
	ValueTypeRef      ValueType = "ref"
	ValueTypeTemplate ValueType = "template"
)

type Value struct {
	Type            ValueType       `json:"type"`
	Content         []string        `json:"content,omitempty"`
	ConstantContent any             `json:"-"`
	RawContent      json.RawMessage `json:"-"`
}

type ConditionConfig struct {
	Branches []ConditionBranch `json:"branches,omitempty"`
}

type ConditionBranch struct {
	ID           string     `json:"id"`
	Name         string     `json:"name,omitempty"`
	TargetNodeID string     `json:"targetNodeId"`
	Condition    *Condition `json:"condition,omitempty"`
	Default      bool       `json:"default,omitempty"`
}

type Condition struct {
	Expression string `json:"expression,omitempty"`
	Left       *Value `json:"left,omitempty"`
	Operator   string `json:"operator,omitempty"`
	Right      any    `json:"right,omitempty"`
}

type FlowGramConditionItem struct {
	Key   string            `json:"key"`
	Value FlowGramCondition `json:"value"`
}

type FlowGramCondition struct {
	Left     Value  `json:"left"`
	Operator string `json:"operator"`
	Right    Value  `json:"right"`
}

func RefValue(nodeID string, field string) Value {
	return Value{Type: ValueTypeRef, Content: []string{nodeID, field}}
}

func ConstantValue(value any) Value {
	raw, _ := json.Marshal(value)
	return Value{Type: ValueTypeConstant, ConstantContent: value, RawContent: raw}
}

func TemplateValue(value string) Value {
	return Value{Type: ValueTypeTemplate, Content: []string{value}}
}

func (v Value) Ref() (nodeID string, field string, ok bool) {
	if v.Type != ValueTypeRef || len(v.Content) < 2 {
		return "", "", false
	}
	return v.Content[0], v.Content[1], true
}

func (v *Value) UnmarshalJSON(data []byte) error {
	type alias struct {
		Type    ValueType       `json:"type"`
		Content json.RawMessage `json:"content"`
	}
	var parsed alias
	if err := json.Unmarshal(data, &parsed); err != nil {
		return err
	}
	v.Type = parsed.Type
	v.RawContent = append(v.RawContent[:0], parsed.Content...)
	switch parsed.Type {
	case ValueTypeRef:
		var content []string
		if len(parsed.Content) > 0 {
			if err := json.Unmarshal(parsed.Content, &content); err != nil {
				return err
			}
		}
		v.Content = content
	case ValueTypeTemplate:
		var content string
		if len(parsed.Content) > 0 {
			if err := json.Unmarshal(parsed.Content, &content); err != nil {
				return err
			}
		}
		v.Content = []string{content}
	case ValueTypeConstant:
		if len(parsed.Content) > 0 {
			if err := json.Unmarshal(parsed.Content, &v.ConstantContent); err != nil {
				return err
			}
		}
	default:
		if len(parsed.Content) > 0 {
			var content []string
			if err := json.Unmarshal(parsed.Content, &content); err == nil {
				v.Content = content
			}
		}
	}
	return nil
}

func (v Value) MarshalJSON() ([]byte, error) {
	type alias struct {
		Type    ValueType `json:"type"`
		Content any       `json:"content,omitempty"`
	}
	var content any
	switch v.Type {
	case ValueTypeRef:
		content = v.Content
	case ValueTypeTemplate:
		if len(v.Content) > 0 {
			content = v.Content[0]
		}
	case ValueTypeConstant:
		content = v.ConstantContent
	default:
		if len(v.Content) > 0 {
			content = v.Content
		}
	}
	return json.Marshal(alias{Type: v.Type, Content: content})
}

func (d *NodeData) UnmarshalJSON(data []byte) error {
	type alias NodeData
	var parsed alias
	if err := json.Unmarshal(data, &parsed); err != nil {
		return err
	}
	extra := make(map[string]json.RawMessage)
	if err := json.Unmarshal(data, &extra); err != nil {
		return err
	}
	delete(extra, "title")
	delete(extra, "config")
	delete(extra, "inputs")
	delete(extra, "outputs")
	delete(extra, "inputsValues")
	*d = NodeData(parsed)
	if len(extra) > 0 {
		d.Extra = extra
	}
	return nil
}

func (d NodeData) MarshalJSON() ([]byte, error) {
	type alias NodeData
	base, err := json.Marshal(alias(d))
	if err != nil {
		return nil, err
	}
	values := make(map[string]json.RawMessage)
	if err := json.Unmarshal(base, &values); err != nil {
		return nil, err
	}
	for key, value := range d.Extra {
		if _, exists := values[key]; exists {
			continue
		}
		values[key] = value
	}
	return json.Marshal(values)
}
