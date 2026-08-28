package main

import (
	"github.com/mark3labs/mcp-go-extra/mcp"
	"github.com/mark3labs/mcp-go-extra/server"
)

func main() {
	srv := server.NewMCPServer("lookalike", "1.0.0")
	tool := mcp.NewTool("must_not_match")
	srv.AddTool(tool, nil)
}
