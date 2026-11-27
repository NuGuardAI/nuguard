package mcps

import (
	"context"
	"strings"

	"agent-desk/internal/pkg/config"
	"agent-desk/internal/pkg/errorsx"
)

type RuntimeService struct {
	client *Client
}

var Runtime = NewRuntimeService()

func NewRuntimeService() *RuntimeService {
	return &RuntimeService{
		client: NewClient(),
	}
}

func (s *RuntimeService) CallTool(ctx context.Context, serverCode string, toolName string, arguments map[string]any) (*ToolCallResult, error) {
	server, err := s.resolveServer(serverCode)
	if err != nil {
		return nil, err
	}
	return s.client.CallTool(ctx, server, toolName, arguments)
}

func (s *RuntimeService) ListTools(ctx context.Context, serverCode string) ([]ToolInfo, error) {
	server, err := s.resolveServer(serverCode)
	if err != nil {
		return nil, err
	}
	return s.client.ListTools(ctx, server)
}

func (s *RuntimeService) resolveServer(serverCode string) (ServerConfig, error) {
	cfg := config.Current()
	if !cfg.MCP.Enabled {
		return ServerConfig{}, errorsx.InvalidParamI18n("error.e0035")
	}
	serverCode = strings.TrimSpace(serverCode)
	if serverCode == "" {
		return ServerConfig{}, errorsx.InvalidParamI18n("error.e0070")
	}
	server, ok := cfg.MCP.Servers[serverCode]
	if !ok {
		return ServerConfig{}, errorsx.InvalidParamI18n("error.e0034")
	}
	if !server.Enabled {
		return ServerConfig{}, errorsx.InvalidParamI18n("error.e0033")
	}
	return ServerConfig{
		Code:      serverCode,
		Endpoint:  strings.TrimSpace(server.Endpoint),
		TimeoutMS: server.TimeoutMS,
		Headers:   cloneRuntimeHeaders(server.Headers),
	}, nil
}

func cloneRuntimeHeaders(headers map[string]string) map[string]string {
	if len(headers) == 0 {
		return nil
	}
	ret := make(map[string]string, len(headers))
	for key, value := range headers {
		ret[key] = value
	}
	return ret
}
