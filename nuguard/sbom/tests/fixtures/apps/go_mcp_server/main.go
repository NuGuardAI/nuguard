package main

import (
	"context"

	"github.com/mark3labs/mcp-go/mcp"
	"github.com/mark3labs/mcp-go/server"
)

const serverName = "security-tools"

func main() {
	srv := server.NewMCPServer(
		serverName,
		"1.0.0",
		server.WithToolCapabilities(false),
	)

	weatherTool := mcp.NewTool(
		"get_weather",
		mcp.WithDescription("Get the weather for a city"),
		mcp.WithString("city", mcp.Required()),
	)
	srv.AddTool(weatherTool, handleWeather)

	srv.AddTool(
		mcp.NewTool(
			"search_docs",
			mcp.WithDescription("Search internal documentation"),
		),
		handleSearch,
	)
}

func handleWeather(
	ctx context.Context,
	request mcp.CallToolRequest,
) (*mcp.CallToolResult, error) {
	return mcp.NewToolResultText("sunny"), nil
}

func handleSearch(
	ctx context.Context,
	request mcp.CallToolRequest,
) (*mcp.CallToolResult, error) {
	return mcp.NewToolResultText("result"), nil
}
