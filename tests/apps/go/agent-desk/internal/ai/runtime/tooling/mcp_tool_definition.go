package tooling

import (
	"fmt"
	"hash/crc32"
	"regexp"
	"strings"
)

var toolNameSanitizer = regexp.MustCompile(`[^a-zA-Z0-9_]`)

type MCPToolDefinition struct {
	ToolCode    string
	ServerCode  string
	ToolName    string
	ModelName   string
	Title       string
	Description string
	FixedArgs   map[string]string
}

func BuildModelToolName(definition MCPToolDefinition) string {
	if strings.TrimSpace(definition.ModelName) != "" {
		return strings.TrimSpace(definition.ModelName)
	}
	base := "mcp_" + strings.TrimSpace(definition.ServerCode) + "_" + strings.TrimSpace(definition.ToolName)
	base = toolNameSanitizer.ReplaceAllString(base, "_")
	base = strings.Trim(base, "_")
	if base == "" {
		base = "mcp_tool"
	}
	checksum := crc32.ChecksumIEEE([]byte(definition.ToolCode))
	return fmt.Sprintf("%s_%08x", base, checksum)
}
