package tooling

import (
	"strings"
	"testing"

	"agent-desk/internal/ai/mcps"
)

func TestBuildReducedToolResultSummaryDeduplicatesStructuredAndTextContent(t *testing.T) {
	result := &mcps.ToolCallResult{
		StructuredContent: map[string]any{
			"timestamp": "2026-07-28 11:51:52",
			"timezone":  "Local",
		},
		Content: []mcps.ToolResultContent{{
			Type: "text",
			Text: `{"timezone":"Local","timestamp":"2026-07-28 11:51:52"}`,
		}},
	}

	summary := BuildReducedToolResultSummary(result)
	if strings.Count(summary, "timestamp") != 1 {
		t.Fatalf("duplicate MCP result was not removed: %q", summary)
	}
	if summary != `{"timestamp":"2026-07-28 11:51:52","timezone":"Local"}` {
		t.Fatalf("unexpected reduced result: %q", summary)
	}
}

func TestBuildReducedToolResultSummaryKeepsDistinctSegments(t *testing.T) {
	result := &mcps.ToolCallResult{
		StructuredContent: map[string]any{"status": "ok"},
		Content: []mcps.ToolResultContent{{
			Type: "text",
			Text: "additional context",
		}},
	}

	summary := BuildReducedToolResultSummary(result)
	if !strings.Contains(summary, `{"status":"ok"}`) || !strings.Contains(summary, "additional context") {
		t.Fatalf("distinct MCP result segments were lost: %q", summary)
	}
}
