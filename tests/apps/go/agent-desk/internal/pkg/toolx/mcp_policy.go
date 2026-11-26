package toolx

import (
	"strings"

	"agent-desk/internal/pkg/dto/request"
)

const (
	MCPRiskLevelRead  = "read"
	MCPRiskLevelWrite = "write"
)

type TrustedMCPToolPolicy struct {
	ToolCode            string
	Title               string
	RiskLevel           string
	RequireConfirmation bool
}

var trustedMCPToolPolicies = map[string]TrustedMCPToolPolicy{
	"system/server_time": {
		ToolCode:            "system/server_time",
		Title:               "获取当前时间",
		RiskLevel:           MCPRiskLevelRead,
		RequireConfirmation: false,
	},
	"system/service_info": {
		ToolCode:            "system/service_info",
		Title:               "查看服务信息",
		RiskLevel:           MCPRiskLevelRead,
		RequireConfirmation: false,
	},
}

func GetTrustedMCPToolPolicy(toolCode string) (TrustedMCPToolPolicy, bool) {
	policy, ok := trustedMCPToolPolicies[NormalizeToolCodeAlias(strings.TrimSpace(toolCode))]
	return policy, ok
}

func ApplyTrustedMCPToolPolicy(item request.AIAgentMCPToolRequest) request.AIAgentMCPToolRequest {
	policy, ok := GetTrustedMCPToolPolicy(item.ToolCode)
	if !ok {
		return item
	}
	item.ToolCode = policy.ToolCode
	item.Title = policy.Title
	item.RiskLevel = policy.RiskLevel
	item.RequireConfirmation = policy.RequireConfirmation
	return item
}
