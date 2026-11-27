package tooling

import (
	"context"
	"fmt"
	"strings"
	"time"

	"agent-desk/internal/ai/mcps"
	"agent-desk/internal/pkg/toolx"
)

// MCPExecutor is the single execution boundary for dynamically discovered
// MCP tools. Engine adapters supply the policy for the current Agent run.
type MCPExecutor struct {
	registry *Registry
	runtime  *mcps.RuntimeService
}

var DefaultMCPExecutor = NewMCPExecutor(DefaultRegistry, mcps.Runtime)

func NewMCPExecutor(registry *Registry, runtime *mcps.RuntimeService) *MCPExecutor {
	return &MCPExecutor{registry: registry, runtime: runtime}
}

func (e *MCPExecutor) Execute(ctx context.Context, toolCode string, arguments map[string]any, policy Policy) (Definition, *mcps.ToolCallResult, error) {
	definition, err := e.registry.Resolve(toolCode)
	if err != nil {
		return Definition{}, nil, err
	}
	if err := DefaultPolicyGuard.Authorize(Invocation{Definition: definition, Arguments: arguments, Policy: policy}); err != nil {
		return Definition{}, nil, err
	}
	serverCode, toolName := toolx.SplitMCPToolCode(strings.TrimSpace(definition.Code))
	if serverCode == "" || toolName == "" {
		return Definition{}, nil, &UnsupportedExecutionError{ToolCode: definition.Code}
	}
	if e.runtime == nil {
		return Definition{}, nil, fmt.Errorf("MCP executor runtime is not configured")
	}
	if definition.TimeoutMS > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, time.Duration(definition.TimeoutMS)*time.Millisecond)
		defer cancel()
	}
	result, err := e.runtime.CallTool(ctx, serverCode, toolName, cloneArguments(arguments))
	return definition, result, err
}

type UnsupportedExecutionError struct {
	ToolCode string
}

func (e *UnsupportedExecutionError) Error() string {
	return "tool is not executable through MCP: " + e.ToolCode
}

func cloneArguments(input map[string]any) map[string]any {
	if len(input) == 0 {
		return map[string]any{}
	}
	ret := make(map[string]any, len(input))
	for key, value := range input {
		ret[key] = value
	}
	return ret
}
