package main

import (
	util "github.com/cloudwego/eino-extra/components/tool/utils"
	cmp "github.com/cloudwego/eino-extra/compose"
)

func main() {
	graph := cmp.NewGraph[string, string]()
	tool := util.NewTool("must_not_match")
	graph.AddToolsNode("tools", tool)
}
