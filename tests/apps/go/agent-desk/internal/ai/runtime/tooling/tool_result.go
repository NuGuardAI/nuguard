package tooling

import (
	"encoding/json"
	"strings"
)

type ToolResult struct {
	Handled     bool   `json:"handled"`
	Terminal    bool   `json:"terminal"`
	Action      string `json:"action"`
	ReplyText   string `json:"replyText,omitempty"`
	ReplySent   bool   `json:"replySent,omitempty"`
	ShouldRetry bool   `json:"shouldRetry"`
}

func MarshalToolResult(result ToolResult) string {
	result.Action = strings.TrimSpace(result.Action)
	result.ReplyText = strings.TrimSpace(result.ReplyText)
	buf, err := json.Marshal(result)
	if err != nil {
		return ""
	}
	return string(buf)
}

func ParseToolResult(raw string) (ToolResult, bool) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return ToolResult{}, false
	}
	var result ToolResult
	if err := json.Unmarshal([]byte(raw), &result); err != nil {
		return ToolResult{}, false
	}
	result.Action = strings.TrimSpace(result.Action)
	result.ReplyText = strings.TrimSpace(result.ReplyText)
	if result.Action == "" && result.ReplyText == "" && !result.Handled && !result.Terminal {
		return ToolResult{}, false
	}
	return result, true
}
