package main

import (
	"context"

	"github.com/cloudwego/eino-ext/components/model/openai"
	einoprompt "github.com/cloudwego/eino/components/prompt"
	basetool "github.com/cloudwego/eino/components/tool"
	util "github.com/cloudwego/eino/components/tool/utils"
	cmp "github.com/cloudwego/eino/compose"
	"github.com/cloudwego/eino/schema"
)

type WeatherInput struct {
	City string `json:"city"`
}

type WeatherOutput struct {
	Forecast string `json:"forecast"`
}

func main() {
	ctx := context.Background()
	chatModel, _ := openai.NewChatModel(
		ctx,
		&openai.ChatModelConfig{Model: "gpt-4o"},
	)
	chatTemplate := einoprompt.FromMessages(
		schema.FString,
		schema.SystemMessage("You are a {role}."),
		schema.UserMessage("Question: {question}"),
	)
	toolInfo := &schema.ToolInfo{
		Name: "lookup_weather",
		Desc: "Look up current weather",
	}
	weatherTool := util.NewTool[*WeatherInput, *WeatherOutput](
		toolInfo,
		lookupWeather,
	)
	toolsNode, _ := cmp.NewToolNode(
		ctx,
		&cmp.ToolsNodeConfig{
			Tools: []basetool.BaseTool{weatherTool},
		},
	)

	graph := cmp.NewGraph[string, string]()
	graph.AddChatModelNode("generate", chatModel)
	graph.AddChatTemplateNode("prompt", chatTemplate)
	graph.AddToolsNode("tools", toolsNode)
}

func lookupWeather(
	ctx context.Context,
	input *WeatherInput,
) (*WeatherOutput, error) {
	return &WeatherOutput{Forecast: "sunny"}, nil
}
