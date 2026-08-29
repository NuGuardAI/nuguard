package main

import (
	"github.com/tmc/langchaingo/agents"
	"github.com/tmc/langchaingo/llms/openai"
	"github.com/tmc/langchaingo/tools"
)

type WeatherTool struct{}

func main() {
	llm, err := openai.New(openai.WithModel("gpt-4o-mini"))
	calculator := tools.Calculator{}
	agentTools := []tools.Tool{
		calculator,
		WeatherTool{},
		tools.WebSearch{APIKey: "test-key"},
	}
	agent := agents.NewOneShotAgent(llm, agentTools)
	_, _, _ = err, agent, calculator
}
