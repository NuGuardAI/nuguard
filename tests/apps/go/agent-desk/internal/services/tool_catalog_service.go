package services

import (
	"context"
	"log/slog"
	"slices"
	"strings"

	"agent-desk/internal/ai/mcps"
	"agent-desk/internal/pkg/config"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/i18nx"
	"agent-desk/internal/pkg/toolx"
)

var ToolCatalogService = newToolCatalogService()

func newToolCatalogService() *toolCatalogService {
	return &toolCatalogService{}
}

type toolCatalogService struct{}

type MCPToolCatalogItem struct {
	ToolCode            string
	ServerCode          string
	ToolName            string
	SourceType          enums.ToolSourceType
	AutoInjected        bool
	Title               string
	Description         string
	InputSchema         any
	OutputSchema        any
	RiskLevel           string
	RequireConfirmation bool
	RiskEditable        bool
}

func (s *toolCatalogService) ListMCPTools(ctx context.Context) ([]MCPToolCatalogItem, error) {
	return s.ListMCPToolsWithLocale(ctx, i18nx.DefaultLocale)
}

func (s *toolCatalogService) ListMCPToolsWithLocale(ctx context.Context, locale string) ([]MCPToolCatalogItem, error) {
	cfg := config.Current()
	ret := make([]MCPToolCatalogItem, 0, 3)
	for _, spec := range toolx.ListAgentDirectToolSpecs() {
		if spec.Code == toolx.BuiltinToolSearch.Code && !cfg.MCP.Enabled {
			continue
		}
		ret = append(ret, MCPToolCatalogItem{
			ToolCode:     spec.Code,
			ServerCode:   spec.ServerCode,
			ToolName:     spec.Name,
			SourceType:   spec.SourceType,
			AutoInjected: spec.AutoInjected,
			Title:        toolx.GetRegisteredToolTitleLocale(spec.Code, locale),
			Description:  toolx.GetRegisteredToolDescriptionLocale(spec.Code, locale),
		})
	}
	if !cfg.MCP.Enabled {
		return ret, nil
	}
	serverCodes := make([]string, 0, len(cfg.MCP.Servers))
	for serverCode, server := range cfg.MCP.Servers {
		if !server.Enabled {
			continue
		}
		serverCodes = append(serverCodes, serverCode)
	}
	slices.Sort(serverCodes)
	for _, serverCode := range serverCodes {
		tools, err := mcps.Runtime.ListTools(ctx, serverCode)
		if err != nil {
			slog.Warn("list mcp server tools failed, skipping server", "server", serverCode, "error", err)
			continue
		}
		for _, item := range tools {
			toolCode := toolx.BuildMCPToolCode(serverCode, item.Name)
			title := strings.TrimSpace(item.Title)
			riskLevel := toolx.MCPRiskLevelWrite
			requireConfirmation := true
			riskEditable := true
			if item.ReadOnlyHint {
				riskLevel = toolx.MCPRiskLevelRead
				requireConfirmation = false
			}
			if policy, ok := toolx.GetTrustedMCPToolPolicy(toolCode); ok {
				title = policy.Title
				riskLevel = policy.RiskLevel
				requireConfirmation = policy.RequireConfirmation
				riskEditable = false
			}
			if title == "" {
				title = strings.TrimSpace(item.Name)
			}
			ret = append(ret, MCPToolCatalogItem{
				ToolCode:            toolCode,
				ServerCode:          serverCode,
				ToolName:            strings.TrimSpace(item.Name),
				SourceType:          enums.ToolSourceTypeMCP,
				AutoInjected:        false,
				Title:               title,
				Description:         strings.TrimSpace(item.Description),
				InputSchema:         item.InputSchema,
				OutputSchema:        item.OutputSchema,
				RiskLevel:           riskLevel,
				RequireConfirmation: requireConfirmation,
				RiskEditable:        riskEditable,
			})
		}
	}
	return ret, nil
}

func (s *toolCatalogService) ValidateMCPToolCode(toolCode string) error {
	return s.ValidateToolCode(toolCode)
}

func (s *toolCatalogService) ValidateToolCode(toolCode string) error {
	cfg := config.Current()
	toolCode = strings.TrimSpace(toolCode)
	if toolCode == "" {
		return errorsx.InvalidParamI18n("error.e0074")
	}
	if toolx.IsAgentDirectToolCode(toolCode) {
		return nil
	}
	serverCode, toolName := toolx.SplitMCPToolCode(toolCode)
	if serverCode == "" || toolName == "" {
		return errorsx.InvalidParamI18n("error.e0075")
	}
	if !cfg.MCP.Enabled {
		return errorsx.InvalidParamI18n("error.e0035")
	}
	server, ok := cfg.MCP.Servers[serverCode]
	if !ok || !server.Enabled {
		return errorsx.InvalidParamI18n("error.e0073")
	}
	return nil
}
