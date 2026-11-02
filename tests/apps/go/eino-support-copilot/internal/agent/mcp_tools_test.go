package agent

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"sort"
	"testing"
	"time"

	"github.com/cloudwego/eino/components/tool"
	"github.com/cloudwego/eino/schema"
	"github.com/eino-contrib/jsonschema"
	"github.com/guiyangyuan/eino-support-copilot/internal/mcpserver"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestLoadMCPToolsUsesStreamableHTTPAllowlist(t *testing.T) {
	mux := http.NewServeMux()
	mux.Handle("/mcp", mcpserver.NewHTTPHandler(mcpserver.NewService(nil)))
	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)

	toolset, err := LoadMCPTools(context.Background(), server.URL+"/mcp")
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, toolset.Close()) })

	names := make([]string, 0, len(toolset.Tools))
	for _, item := range toolset.Tools {
		info, infoErr := item.Info(context.Background())
		require.NoError(t, infoErr)
		names = append(names, info.Name)
	}
	sort.Strings(names)
	assert.Equal(t, []string{"get_account_status", "get_payment_status", "get_service_status"}, names)
}

func TestTimeoutToolRetriesOnlyTemporaryUnavailableOnce(t *testing.T) {
	inner := &sequenceTool{name: "generated_status", errors: []error{errors.New("TEMPORARY_UNAVAILABLE"), nil}}
	wrapper := newTimeoutTool(inner, time.Second, 1)

	result, err := wrapper.InvokableRun(context.Background(), `{}`)

	require.NoError(t, err)
	assert.Equal(t, "ok", result)
	assert.Equal(t, 2, inner.calls)
}

func TestTimeoutToolDoesNotRetryPermanentError(t *testing.T) {
	inner := &sequenceTool{name: "generated_status", errors: []error{errors.New("NOT_FOUND")}}
	wrapper := newTimeoutTool(inner, time.Second, 1)

	_, err := wrapper.InvokableRun(context.Background(), `{}`)

	assert.Error(t, err)
	assert.Equal(t, 1, inner.calls)
}

type sequenceTool struct {
	name   string
	errors []error
	calls  int
}

func (t *sequenceTool) Info(context.Context) (*schema.ToolInfo, error) {
	return &schema.ToolInfo{
		Name: t.name,
		Desc: "Generated status",
		ParamsOneOf: schema.NewParamsOneOfByJSONSchema(&jsonschema.Schema{
			Type: "object",
		}),
	}, nil
}

func (t *sequenceTool) InvokableRun(context.Context, string, ...tool.Option) (string, error) {
	index := t.calls
	t.calls++
	if index < len(t.errors) && t.errors[index] != nil {
		return "", t.errors[index]
	}
	return "ok", nil
}
