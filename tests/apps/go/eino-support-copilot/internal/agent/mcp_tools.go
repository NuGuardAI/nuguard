package agent

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	mcpp "github.com/cloudwego/eino-ext/components/tool/mcp"
	"github.com/cloudwego/eino/components/tool"
	"github.com/cloudwego/eino/schema"
	"github.com/mark3labs/mcp-go/client"
	"github.com/mark3labs/mcp-go/mcp"
)

var mcpToolAllowlist = []string{
	"get_account_status",
	"get_payment_status",
	"get_service_status",
}

type MCPToolset struct {
	Tools  []tool.BaseTool
	client *client.Client
}

func LoadMCPTools(ctx context.Context, endpoint string) (*MCPToolset, error) {
	cli, err := client.NewStreamableHttpClient(strings.TrimSpace(endpoint))
	if err != nil {
		return nil, fmt.Errorf("create MCP client: %w", err)
	}
	if err := cli.Start(ctx); err != nil {
		_ = cli.Close()
		return nil, fmt.Errorf("start MCP client: %w", err)
	}
	request := mcp.InitializeRequest{}
	request.Params.ProtocolVersion = mcp.LATEST_PROTOCOL_VERSION
	request.Params.ClientInfo = mcp.Implementation{Name: "support-copilot-api", Version: "0.1.0"}
	if _, err := cli.Initialize(ctx, request); err != nil {
		_ = cli.Close()
		return nil, fmt.Errorf("initialize MCP client: %w", err)
	}
	loaded, err := mcpp.GetTools(ctx, &mcpp.Config{
		Cli:          cli,
		ToolNameList: mcpToolAllowlist,
	})
	if err != nil {
		_ = cli.Close()
		return nil, fmt.Errorf("load Eino MCP tools: %w", err)
	}
	if len(loaded) != len(mcpToolAllowlist) {
		_ = cli.Close()
		return nil, fmt.Errorf("load Eino MCP tools: expected %d, got %d", len(mcpToolAllowlist), len(loaded))
	}
	wrapped := make([]tool.BaseTool, 0, len(loaded))
	for _, item := range loaded {
		wrapped = append(wrapped, newTimeoutTool(item, 2*time.Second, 1))
	}
	return &MCPToolset{Tools: wrapped, client: cli}, nil
}

func (s *MCPToolset) Close() error {
	if s == nil || s.client == nil {
		return nil
	}
	return s.client.Close()
}

type timeoutTool struct {
	inner      tool.BaseTool
	timeout    time.Duration
	maxRetries int
}

func newTimeoutTool(inner tool.BaseTool, timeout time.Duration, maxRetries int) *timeoutTool {
	return &timeoutTool{inner: inner, timeout: timeout, maxRetries: maxRetries}
}

func (t *timeoutTool) Info(ctx context.Context) (*schema.ToolInfo, error) {
	return t.inner.Info(ctx)
}

func (t *timeoutTool) InvokableRun(ctx context.Context, arguments string, opts ...tool.Option) (string, error) {
	invokable, ok := t.inner.(tool.InvokableTool)
	if !ok {
		return "", errors.New("MCP tool is not invokable")
	}
	var lastErr error
	for attempt := 0; attempt <= t.maxRetries; attempt++ {
		attemptCtx, cancel := context.WithTimeout(ctx, t.timeout)
		result, err := invokable.InvokableRun(attemptCtx, arguments, opts...)
		cancel()
		if err == nil {
			return result, nil
		}
		lastErr = err
		if !strings.Contains(err.Error(), "TEMPORARY_UNAVAILABLE") {
			break
		}
	}
	return "", lastErr
}
