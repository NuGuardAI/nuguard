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

type HandoffGraphTool struct {
	conversation models.Conversation
	aiAgent      models.AIAgent
}

func NewHandoffGraphTool() *HandoffGraphTool {
	return &HandoffGraphTool{}
}

func (t *HandoffGraphTool) Spec() toolx.ToolSpec {
	return toolx.GraphHandoffConversation
}

func (t *HandoffGraphTool) Name() string {
	return toolx.GraphHandoffConversation.Name
}

func (t *HandoffGraphTool) Code() string {
	return toolx.GraphHandoffConversation.Code
}

func (t *HandoffGraphTool) Enabled(ctx registry.Context) bool {
	return true
}

func (t *HandoffGraphTool) Build(ctx registry.Context) (einotool.BaseTool, error) {
	if !t.Enabled(ctx) {
		return nil, nil
	}
	return &HandoffGraphTool{
		conversation: ctx.Conversation,
		aiAgent:      ctx.AIAgent,
	}, nil
}

func (t *HandoffGraphTool) Info(ctx context.Context) (*schema.ToolInfo, error) {
	return &schema.ToolInfo{
		Name: toolx.GraphHandoffConversation.Name,
		Desc: i18nx.Get("tool.graph.handoffConversation.info"),
		ParamsOneOf: schema.NewParamsOneOfByJSONSchema(&einojsonschema.Schema{
			Version: einojsonschema.Version,
			Type:    "object",
			Properties: orderedmap.New[string, *einojsonschema.Schema](orderedmap.WithInitialData(
				orderedmap.Pair[string, *einojsonschema.Schema]{
					Key: "reason",
					Value: &einojsonschema.Schema{
						Type:        "string",
						Description: i18nx.Get("tool.graph.handoffConversation.param.reason"),
					},
				},
			)),
		}),
		Extra: map[string]any{
			"toolCode":   toolx.GraphHandoffConversation.Code,
			"sourceType": toolx.GraphHandoffConversation.SourceType,
		},
	}, nil
}

func (t *HandoffGraphTool) InvokableRun(ctx context.Context, argumentsInJSON string, opts ...einotool.Option) (string, error) {
	return graphs.NewHandoffGraph(t.conversation, t.aiAgent).Run(ctx, argumentsInJSON)
}
