package mcpserver

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"

	"github.com/mark3labs/mcp-go/mcp"
	markserver "github.com/mark3labs/mcp-go/server"
)

func NewHTTPHandler(service *Service) http.Handler {
	server := markserver.NewMCPServer(
		"eino-support-copilot-business-data",
		"1.0.0",
		markserver.WithToolCapabilities(false),
		markserver.WithInstructions(
			"Read-only access to generated support demo data. Never infer fields not returned by a tool.",
		),
	)

	server.AddTool(
		mcp.NewTool(
			"get_account_status",
			toolOptions(
				"Read a generated customer's plan, account state, and subscription synchronization state.",
				mcp.WithInputSchema[AccountStatusInput](),
			)...,
		),
		mcp.NewTypedToolHandler(func(ctx context.Context, _ mcp.CallToolRequest, input AccountStatusInput) (*mcp.CallToolResult, error) {
			result, err := service.GetAccountStatus(ctx, input)
			return encodeToolResult(result, err)
		}),
	)

	server.AddTool(
		mcp.NewTool(
			"get_payment_status",
			toolOptions(
				"Read the latest or specified generated payment status for an account.",
				mcp.WithInputSchema[PaymentStatusInput](),
			)...,
		),
		mcp.NewTypedToolHandler(func(ctx context.Context, _ mcp.CallToolRequest, input PaymentStatusInput) (*mcp.CallToolResult, error) {
			result, err := service.GetPaymentStatus(ctx, input)
			return encodeToolResult(result, err)
		}),
	)

	server.AddTool(
		mcp.NewTool(
			"get_service_status",
			toolOptions(
				"Read the latest generated operational status for a named service.",
				mcp.WithInputSchema[ServiceStatusInput](),
			)...,
		),
		mcp.NewTypedToolHandler(func(ctx context.Context, _ mcp.CallToolRequest, input ServiceStatusInput) (*mcp.CallToolResult, error) {
			result, err := service.GetServiceStatus(ctx, input)
			return encodeToolResult(result, err)
		}),
	)

	return markserver.NewStreamableHTTPServer(server, markserver.WithStateLess(true))
}

func toolOptions(description string, schema mcp.ToolOption) []mcp.ToolOption {
	return []mcp.ToolOption{
		mcp.WithDescription(description),
		schema,
		mcp.WithReadOnlyHintAnnotation(true),
		mcp.WithDestructiveHintAnnotation(false),
		mcp.WithIdempotentHintAnnotation(true),
		mcp.WithOpenWorldHintAnnotation(false),
	}
}

func encodeToolResult[T any](value T, err error) (*mcp.CallToolResult, error) {
	if err != nil {
		var toolErr *ToolError
		if !errors.As(err, &toolErr) {
			toolErr = temporaryUnavailable()
		}
		encoded, marshalErr := json.Marshal(toolErr)
		if marshalErr != nil {
			return mcp.NewToolResultError(`{"code":"TEMPORARY_UNAVAILABLE","message":"unable to encode tool response","retryable":true}`), nil
		}
		return mcp.NewToolResultError(string(encoded)), nil
	}
	return mcp.NewToolResultJSON(value)
}
