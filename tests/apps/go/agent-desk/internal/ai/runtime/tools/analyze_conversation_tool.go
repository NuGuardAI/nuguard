package tools

import (
	"context"

	"agent-desk/internal/ai/runtime/graphs"
	"agent-desk/internal/ai/runtime/registry"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/i18nx"
	"agent-desk/internal/pkg/toolx"

	einotool "github.com/cloudwego/eino/components/tool"
	"github.com/cloudwego/eino/schema"
	einojsonschema "github.com/eino-contrib/jsonschema"
	orderedmap "github.com/wk8/go-ordered-map/v2"
)

type AnalyzeConversationTool struct {
	conversation models.Conversation
}

func NewAnalyzeConversationTool() *AnalyzeConversationTool {
	return &AnalyzeConversationTool{}
}

func (t *AnalyzeConversationTool) Spec() toolx.ToolSpec {
	return toolx.GraphAnalyzeConversation
}

func (t *AnalyzeConversationTool) Name() string {
	return toolx.GraphAnalyzeConversation.Name
}

func (t *AnalyzeConversationTool) Code() string {
	return toolx.GraphAnalyzeConversation.Code
}

func (t *AnalyzeConversationTool) Enabled(ctx registry.Context) bool {
	return true
}

func (t *AnalyzeConversationTool) Build(ctx registry.Context) (einotool.BaseTool, error) {
	if !t.Enabled(ctx) {
		return nil, nil
	}
	return &AnalyzeConversationTool{conversation: ctx.Conversation}, nil
}

func (t *AnalyzeConversationTool) Info(ctx context.Context) (*schema.ToolInfo, error) {
	return &schema.ToolInfo{
		Name: toolx.GraphAnalyzeConversation.Name,
		Desc: i18nx.Get("tool.graph.analyzeConversation.info"),
		ParamsOneOf: schema.NewParamsOneOfByJSONSchema(&einojsonschema.Schema{
			Version: einojsonschema.Version,
			Type:    "object",
			Properties: orderedmap.New[string, *einojsonschema.Schema](orderedmap.WithInitialData(
				orderedmap.Pair[string, *einojsonschema.Schema]{
					Key: "goal",
					Value: &einojsonschema.Schema{
						Type:        "string",
						Description: i18nx.Get("tool.graph.analyzeConversation.param.goal"),
					},
				},
				orderedmap.Pair[string, *einojsonschema.Schema]{
					Key: "observedIssue",
					Value: &einojsonschema.Schema{
						Type:        "string",
						Description: i18nx.Get("tool.graph.analyzeConversation.param.observedIssue"),
					},
				},
				orderedmap.Pair[string, *einojsonschema.Schema]{
					Key: "needTicket",
					Value: &einojsonschema.Schema{
						Type:        "boolean",
						Description: i18nx.Get("tool.graph.triageServiceRequest.param.needTicket"),
					},
				},
				orderedmap.Pair[string, *einojsonschema.Schema]{
					Key: "needHumanHandoff",
					Value: &einojsonschema.Schema{
						Type:        "boolean",
						Description: i18nx.Get("tool.graph.triageServiceRequest.param.needHumanHandoff"),
					},
				},
				orderedmap.Pair[string, *einojsonschema.Schema]{
					Key: "needQualityCheck",
					Value: &einojsonschema.Schema{
						Type:        "boolean",
						Description: i18nx.Get("tool.graph.analyzeConversation.param.needQualityCheck"),
					},
				},
				orderedmap.Pair[string, *einojsonschema.Schema]{
					Key: "additionalContext",
					Value: &einojsonschema.Schema{
						Type:        "string",
						Description: i18nx.Get("tool.graph.analyzeConversation.param.additionalContext"),
					},
				},
			)),
		}),
		Extra: map[string]any{
			"toolCode":   toolx.GraphAnalyzeConversation.Code,
			"sourceType": toolx.GraphAnalyzeConversation.SourceType,
		},
	}, nil
}

func (t *AnalyzeConversationTool) InvokableRun(ctx context.Context, argumentsInJSON string, opts ...einotool.Option) (string, error) {
	return graphs.NewAnalyzeConversationGraph(t.conversation).Run(ctx, argumentsInJSON)
}
