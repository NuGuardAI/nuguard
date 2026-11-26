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

type TriageServiceRequestTool struct {
	conversation models.Conversation
}

func NewTriageServiceRequestTool() *TriageServiceRequestTool {
	return &TriageServiceRequestTool{}
}

func (t *TriageServiceRequestTool) Spec() toolx.ToolSpec {
	return toolx.GraphTriageServiceRequest
}

func (t *TriageServiceRequestTool) Name() string {
	return toolx.GraphTriageServiceRequest.Name
}

func (t *TriageServiceRequestTool) Code() string {
	return toolx.GraphTriageServiceRequest.Code
}

func (t *TriageServiceRequestTool) Enabled(ctx registry.Context) bool {
	return true
}

func (t *TriageServiceRequestTool) Build(ctx registry.Context) (einotool.BaseTool, error) {
	if !t.Enabled(ctx) {
		return nil, nil
	}
	return &TriageServiceRequestTool{conversation: ctx.Conversation}, nil
}

func (t *TriageServiceRequestTool) Info(ctx context.Context) (*schema.ToolInfo, error) {
	return &schema.ToolInfo{
		Name: toolx.GraphTriageServiceRequest.Name,
		Desc: i18nx.Get("tool.graph.triageServiceRequest.info"),
		ParamsOneOf: schema.NewParamsOneOfByJSONSchema(&einojsonschema.Schema{
			Version: einojsonschema.Version,
			Type:    "object",
			Properties: orderedmap.New[string, *einojsonschema.Schema](orderedmap.WithInitialData(
				orderedmap.Pair[string, *einojsonschema.Schema]{
					Key: "goal",
					Value: &einojsonschema.Schema{
						Type:        "string",
						Description: i18nx.Get("tool.graph.triageServiceRequest.param.goal"),
					},
				},
				orderedmap.Pair[string, *einojsonschema.Schema]{
					Key: "observedIssue",
					Value: &einojsonschema.Schema{
						Type:        "string",
						Description: i18nx.Get("tool.graph.triageServiceRequest.param.observedIssue"),
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
					Key: "additionalContext",
					Value: &einojsonschema.Schema{
						Type:        "string",
						Description: i18nx.Get("tool.graph.triageServiceRequest.param.additionalContext"),
					},
				},
			)),
		}),
		Extra: map[string]any{
			"toolCode":   toolx.GraphTriageServiceRequest.Code,
			"sourceType": "graph",
		},
	}, nil
}

func (t *TriageServiceRequestTool) InvokableRun(ctx context.Context, argumentsInJSON string, opts ...einotool.Option) (string, error) {
	return graphs.NewTriageServiceRequestGraph(t.conversation).Run(ctx, argumentsInJSON)
}
