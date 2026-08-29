package main

import (
	"github.com/tmc/langchaingo-extra/agents"
	"github.com/tmc/langchaingo-extra/llms/openai"
	"github.com/tmc/langchaingo-extra/tools"
)

func main() {
	llm, _ := openai.New(openai.WithModel("must-not-match"))
	toolset := []tools.Tool{tools.Calculator{}}
	agent := agents.NewOneShotAgent(llm, toolset)
	_ = agent
}
