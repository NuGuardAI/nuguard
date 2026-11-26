// Package tooling provides the engine-independent tool governance boundary.
package tooling

import (
	"encoding/json"
	"fmt"
	"strings"

	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/toolx"
)

const (
	RiskLevelRead  = "read"
	RiskLevelWrite = "write"
)

// Definition is the normalized, engine-independent description of a tool.
type Definition struct {
	Code                string
	Name                string
	Description         string
	InputSchema         map[string]any
	SourceType          enums.ToolSourceType
	RiskLevel           string
	RequireConfirmation bool
	MaxCallsPerRun      int
	TimeoutMS           int
	IdempotencyMode     string
}

// Policy is supplied by the caller's agent/runtime context for one invocation.
// An empty AllowedToolCodes means the caller did not impose an allow-list.
type Policy struct {
	AllowedToolCodes      []string
	SkillAllowedToolCodes []string
	AllowedRiskLevels     []string
	CallCount             int
	TotalCallCount        int
	MaxTotalCalls         int
	MaxArgumentBytes      int
	Confirmed             bool
}

type Invocation struct {
	Definition Definition
	Arguments  map[string]any
	Policy     Policy
}

// PolicyGuard is the reusable enforcement point for every engine/tool adapter.
type PolicyGuard struct{}

var DefaultPolicyGuard = &PolicyGuard{}

type Registry struct{}

var DefaultRegistry = NewRegistry()

func NewRegistry() *Registry {
	return &Registry{}
}

func (r *Registry) Resolve(toolCode string) (Definition, error) {
	toolCode = toolx.NormalizeToolCodeAlias(strings.TrimSpace(toolCode))
	if toolCode == "" {
		return Definition{}, fmt.Errorf("tool code is required")
	}
	if spec, ok := toolx.GetRegisteredToolSpec(toolCode); ok {
		return definitionFromSpec(spec), nil
	}
	serverCode, toolName := toolx.SplitMCPToolCode(toolCode)
	if serverCode == "" || toolName == "" {
		return Definition{}, fmt.Errorf("unsupported tool code: %s", toolCode)
	}
	// MCP tools are explicitly selected by an administrator before an Agent can
	// call them. Treat that persisted allow-list as the authorization boundary;
	// only tools with an explicit built-in policy require extra confirmation.
	return Definition{
		Code:                toolCode,
		Name:                toolName,
		InputSchema:         map[string]any{"type": "object", "additionalProperties": true},
		SourceType:          enums.ToolSourceTypeMCP,
		RiskLevel:           RiskLevelRead,
		RequireConfirmation: false,
		MaxCallsPerRun:      3,
		TimeoutMS:           30000,
		IdempotencyMode:     "caller",
	}, nil
}

func (r *Registry) Authorize(definition Definition, policy Policy) error {
	return DefaultPolicyGuard.Authorize(Invocation{Definition: definition, Policy: policy})
}

func (g *PolicyGuard) Authorize(invocation Invocation) error {
	definition := invocation.Definition
	policy := invocation.Policy
	if definition.Code == "" {
		return fmt.Errorf("tool definition is required")
	}
	if len(policy.AllowedToolCodes) > 0 && !containsCanonicalToolCode(policy.AllowedToolCodes, definition.Code) {
		return fmt.Errorf("tool is not allowed: %s", definition.Code)
	}
	if len(policy.SkillAllowedToolCodes) > 0 && !containsCanonicalToolCode(policy.SkillAllowedToolCodes, definition.Code) {
		return fmt.Errorf("tool is not allowed by the selected skill: %s", definition.Code)
	}
	if len(policy.AllowedRiskLevels) > 0 && !containsString(policy.AllowedRiskLevels, definition.RiskLevel) {
		return fmt.Errorf("tool risk level is not allowed: %s", definition.RiskLevel)
	}
	if definition.MaxCallsPerRun > 0 && policy.CallCount >= definition.MaxCallsPerRun {
		return fmt.Errorf("tool call limit reached: %s", definition.Code)
	}
	if policy.MaxTotalCalls > 0 && policy.TotalCallCount >= policy.MaxTotalCalls {
		return fmt.Errorf("total tool call limit reached")
	}
	if policy.MaxArgumentBytes > 0 {
		encoded, err := json.Marshal(invocation.Arguments)
		if err != nil {
			return fmt.Errorf("tool arguments are not serializable: %w", err)
		}
		if len(encoded) > policy.MaxArgumentBytes {
			return fmt.Errorf("tool arguments exceed size limit: %s", definition.Code)
		}
	}
	if definition.RequireConfirmation && !policy.Confirmed {
		return fmt.Errorf("tool confirmation is required: %s", definition.Code)
	}
	return nil
}

func definitionFromSpec(spec toolx.ToolSpec) Definition {
	definition := Definition{
		Code:            spec.Code,
		Name:            spec.Name,
		Description:     spec.Description,
		SourceType:      spec.SourceType,
		RiskLevel:       RiskLevelRead,
		MaxCallsPerRun:  8,
		TimeoutMS:       15000,
		IdempotencyMode: "none",
	}
	switch spec.Code {
	case toolx.BuiltinConversationContext.Code:
		definition.InputSchema = objectSchema(map[string]any{})
	case toolx.BuiltinKnowledgeRetrieve.Code:
		definition.InputSchema = requiredObjectSchema([]string{"query"}, map[string]any{"query": map[string]any{"type": "string"}})
	case toolx.GraphTriageServiceRequest.Code:
		definition.InputSchema = objectSchema(map[string]any{
			"goal":              map[string]any{"type": "string"},
			"observedIssue":     map[string]any{"type": "string"},
			"needTicket":        map[string]any{"type": "boolean"},
			"needHumanHandoff":  map[string]any{"type": "boolean"},
			"additionalContext": map[string]any{"type": "string"},
		})
	case toolx.GraphAnalyzeConversation.Code:
		definition.InputSchema = objectSchema(map[string]any{
			"goal":              map[string]any{"type": "string"},
			"observedIssue":     map[string]any{"type": "string"},
			"needTicket":        map[string]any{"type": "boolean"},
			"needHumanHandoff":  map[string]any{"type": "boolean"},
			"needQualityCheck":  map[string]any{"type": "boolean"},
			"additionalContext": map[string]any{"type": "string"},
		})
	case toolx.GraphPrepareTicketDraft.Code:
		definition.InputSchema = objectSchema(map[string]any{
			"title":           map[string]any{"type": "string"},
			"description":     map[string]any{"type": "string"},
			"issue":           map[string]any{"type": "string"},
			"impact":          map[string]any{"type": "string"},
			"expectedOutcome": map[string]any{"type": "string"},
			"currentAttempt":  map[string]any{"type": "string"},
		})
	case toolx.GraphCreateTicketConfirm.Code:
		definition.RiskLevel = RiskLevelWrite
		definition.RequireConfirmation = true
		definition.MaxCallsPerRun = 1
		definition.IdempotencyMode = "business"
		definition.InputSchema = requiredObjectSchema([]string{"title", "description"}, map[string]any{
			"title": map[string]any{"type": "string"}, "description": map[string]any{"type": "string"},
		})
	case toolx.GraphHandoffConversation.Code:
		definition.RiskLevel = RiskLevelWrite
		definition.RequireConfirmation = true
		definition.MaxCallsPerRun = 1
		definition.IdempotencyMode = "business"
		definition.InputSchema = objectSchema(map[string]any{"reason": map[string]any{"type": "string"}})
	}
	return definition
}

func objectSchema(properties map[string]any) map[string]any {
	return map[string]any{"type": "object", "properties": properties}
}

func requiredObjectSchema(required []string, properties map[string]any) map[string]any {
	schema := objectSchema(properties)
	schema["required"] = required
	return schema
}

func containsString(items []string, target string) bool {
	for _, item := range items {
		if strings.EqualFold(strings.TrimSpace(item), strings.TrimSpace(target)) {
			return true
		}
	}
	return false
}

func containsCanonicalToolCode(items []string, target string) bool {
	target = toolx.NormalizeToolCodeAlias(strings.TrimSpace(target))
	for _, item := range items {
		if toolx.NormalizeToolCodeAlias(strings.TrimSpace(item)) == target {
			return true
		}
	}
	return false
}
