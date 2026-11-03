package mcpserver

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sort"
	"testing"

	"github.com/guiyangyuan/eino-support-copilot/internal/testsupport"
	"github.com/mark3labs/mcp-go/client"
	"github.com/mark3labs/mcp-go/mcp"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestStreamableHTTPProtocolListsAndCallsReadOnlyTools(t *testing.T) {
	pool := testsupport.StartPostgres(t)
	loadBusinessSeed(t, pool)

	mux := http.NewServeMux()
	mux.Handle("/mcp", NewHTTPHandler(NewService(pool)))
	testServer := httptest.NewServer(mux)
	t.Cleanup(testServer.Close)

	cli, err := client.NewStreamableHttpClient(testServer.URL + "/mcp")
	require.NoError(t, err)
	require.NoError(t, cli.Start(context.Background()))
	t.Cleanup(func() { require.NoError(t, cli.Close()) })

	_, err = cli.Initialize(context.Background(), mcp.InitializeRequest{
		Params: mcp.InitializeParams{
			ProtocolVersion: mcp.LATEST_PROTOCOL_VERSION,
			ClientInfo: mcp.Implementation{
				Name:    "support-copilot-test",
				Version: "1.0.0",
			},
		},
	})
	require.NoError(t, err)

	tools, err := cli.ListTools(context.Background(), mcp.ListToolsRequest{})
	require.NoError(t, err)
	names := make([]string, 0, len(tools.Tools))
	for _, tool := range tools.Tools {
		names = append(names, tool.Name)
		require.NotNil(t, tool.Annotations.ReadOnlyHint)
		require.NotNil(t, tool.Annotations.DestructiveHint)
		if tool.Name == "refund_payment" {
			assert.False(t, *tool.Annotations.ReadOnlyHint)
			assert.True(t, *tool.Annotations.DestructiveHint)
			continue
		}
		assert.True(t, *tool.Annotations.ReadOnlyHint)
		assert.False(t, *tool.Annotations.DestructiveHint)
	}
	sort.Strings(names)
	assert.Equal(t, []string{
		"get_account_status",
		"get_payment_status",
		"get_service_status",
		"refund_payment",
	}, names)

	request := mcp.CallToolRequest{}
	request.Params.Name = "get_payment_status"
	request.Params.Arguments = map[string]any{"account_id": "acct_demo_pro"}
	result, err := cli.CallTool(context.Background(), request)
	require.NoError(t, err)
	assert.False(t, result.IsError)
	require.Len(t, result.Content, 1)
	content, ok := result.Content[0].(mcp.TextContent)
	require.True(t, ok)
	var payment PaymentStatus
	require.NoError(t, json.Unmarshal([]byte(content.Text), &payment))
	assert.Equal(t, "pay_demo_paid", payment.PaymentID)
	assert.Equal(t, "paid", payment.Status)
}
