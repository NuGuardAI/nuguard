// Package readtools executes deterministic, read-only graph tools through the
// shared Tool Registry boundary.
package readtools

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"agent-desk/internal/ai/runtime/graphs"
	"agent-desk/internal/ai/runtime/retrievers"
	aitooling "agent-desk/internal/ai/tooling"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/toolx"
)

func ExecuteGraphTool(ctx context.Context, conversation models.Conversation, toolCode string, arguments map[string]any, policy aitooling.Policy) (aitooling.Definition, string, error) {
	toolCode = toolx.NormalizeToolCodeAlias(strings.TrimSpace(toolCode))
	if toolCode != toolx.GraphTriageServiceRequest.Code && toolCode != toolx.GraphAnalyzeConversation.Code && toolCode != toolx.GraphPrepareTicketDraft.Code {
		return aitooling.Definition{}, "", fmt.Errorf("tool is not a graph read tool")
	}
	definition, err := aitooling.DefaultRegistry.Resolve(toolCode)
	if err != nil {
		return aitooling.Definition{}, "", err
	}
	if err := aitooling.DefaultPolicyGuard.Authorize(aitooling.Invocation{
		Definition: definition,
		Arguments:  arguments,
		Policy:     policy,
	}); err != nil {
		return definition, "", err
	}
	if definition.TimeoutMS > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, time.Duration(definition.TimeoutMS)*time.Millisecond)
		defer cancel()
	}
	data, err := json.Marshal(arguments)
	if err != nil {
		return definition, "", err
	}
	switch toolCode {
	case toolx.GraphTriageServiceRequest.Code:
		result, err := graphs.NewTriageServiceRequestGraph(conversation).Run(ctx, string(data))
		return definition, result, err
	case toolx.GraphAnalyzeConversation.Code:
		result, err := graphs.NewAnalyzeConversationGraph(conversation).Run(ctx, string(data))
		return definition, result, err
	default:
		result, err := graphs.NewPrepareTicketDraftGraph(conversation).Run(ctx, string(data))
		return definition, result, err
	}
}

// RetrieveKnowledge executes the built-in knowledge tool after the same
// registry policy and timeout checks used by graph tools.
func RetrieveKnowledge(ctx context.Context, agent models.AIAgent, knowledgeBaseIDs []int64, query string, policy aitooling.Policy) (aitooling.Definition, *retrievers.KnowledgeRetrieveResult, error) {
	definition, err := aitooling.DefaultRegistry.Resolve(toolx.BuiltinKnowledgeRetrieve.Code)
	if err != nil {
		return aitooling.Definition{}, nil, err
	}
	arguments := map[string]any{"query": strings.TrimSpace(query), "knowledgeBaseIds": knowledgeBaseIDs}
	if err := aitooling.DefaultPolicyGuard.Authorize(aitooling.Invocation{
		Definition: definition,
		Arguments:  arguments,
		Policy:     policy,
	}); err != nil {
		return definition, nil, err
	}
	if definition.TimeoutMS > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, time.Duration(definition.TimeoutMS)*time.Millisecond)
		defer cancel()
	}
	result, err := retrievers.NewKnowledgeRetriever(agent, knowledgeBaseIDs).RetrieveContext(ctx, strings.TrimSpace(query))
	return definition, result, err
}
