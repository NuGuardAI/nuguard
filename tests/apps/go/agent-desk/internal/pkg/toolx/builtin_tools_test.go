package toolx

import (
	"testing"

	"agent-desk/internal/pkg/i18nx"
)

func TestResolveToolMetadata(t *testing.T) {
	item := ResolveToolMetadata("builtin/create_ticket_with_confirmation", "")
	if item.ToolCode != GraphCreateTicketConfirm.Code {
		t.Fatalf("unexpected tool code: %s", item.ToolCode)
	}
	if item.ServerCode != GraphCreateTicketConfirm.ServerCode {
		t.Fatalf("unexpected server code: %s", item.ServerCode)
	}
	if item.ToolName != GraphCreateTicketConfirm.Name {
		t.Fatalf("unexpected tool name: %s", item.ToolName)
	}
	if item.SourceType != GraphCreateTicketConfirm.SourceType {
		t.Fatalf("unexpected source type: %s", item.SourceType)
	}
}

func TestResolveToolMetadataFallsBackToName(t *testing.T) {
	item := ResolveToolMetadata("mcp/demo_tool", "demo_tool")
	if item.ToolCode != "mcp/demo_tool" {
		t.Fatalf("unexpected tool code: %s", item.ToolCode)
	}
	if item.ServerCode != "" {
		t.Fatalf("unexpected server code: %s", item.ServerCode)
	}
	if item.ToolName != "demo_tool" {
		t.Fatalf("unexpected tool name: %s", item.ToolName)
	}
	if item.SourceType != "mcp" {
		t.Fatalf("unexpected source type: %s", item.SourceType)
	}
}

func TestRegisteredToolTextUsesEnglishLocale(t *testing.T) {
	title := GetRegisteredToolTitleLocale(GraphCreateTicketConfirm.Code, i18nx.LocaleEnUS)
	if title != "Create Ticket With Confirmation" {
		t.Fatalf("unexpected english title: %q", title)
	}

	description := GetRegisteredToolDescriptionLocale(GraphCreateTicketConfirm.Code, i18nx.LocaleEnUS)
	want := "Guides ticket creation with parameter preparation, customer confirmation, actual ticket creation, and final result delivery."
	if description != want {
		t.Fatalf("unexpected english description: %q", description)
	}
}

func TestRegisteredToolTextKeepsChineseLocale(t *testing.T) {
	title := GetRegisteredToolTitleLocale(GraphCreateTicketConfirm.Code, i18nx.LocaleZhCN)
	if title != "创建工单确认流程" {
		t.Fatalf("unexpected chinese title: %q", title)
	}

	description := GetRegisteredToolDescriptionLocale(GraphCreateTicketConfirm.Code, i18nx.LocaleZhCN)
	if description != "Graph Tool。处理工单参数准备、用户确认、实际创建工单和结果返回。" {
		t.Fatalf("unexpected chinese description: %q", description)
	}
}
