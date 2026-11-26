package builders

import (
	"testing"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/i18nx"
)

func TestBuildNotificationListReturnsEmptySlice(t *testing.T) {
	results := BuildNotificationList([]models.Notification{})

	if results == nil {
		t.Fatalf("expected empty slice, got nil")
	}
	if len(results) != 0 {
		t.Fatalf("expected empty slice, got %d items", len(results))
	}
}

func TestBuildNotificationLocalizesKnownSystemNotification(t *testing.T) {
	result := BuildNotificationWithLocale(&models.Notification{
		Title:            "工单指派提醒",
		Content:          "工单 TK-100 已指派给你\n无法登录后台\n指派原因: 优先处理",
		NotificationType: "ticket_assigned",
	}, i18nx.LocaleEnUS)

	if result == nil {
		t.Fatalf("expected notification response")
	}
	if result.Title != "Ticket assigned" {
		t.Fatalf("title = %q", result.Title)
	}
	wantContent := "Ticket TK-100 has been assigned to you.\n无法登录后台\nAssignment reason: 优先处理"
	if result.Content != wantContent {
		t.Fatalf("content = %q, want %q", result.Content, wantContent)
	}
}
