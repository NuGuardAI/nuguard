package services

import (
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/i18nx"
	"testing"
)

func TestDashboardTextUsesEnglishLocale(t *testing.T) {
	t.Parallel()

	if got := dashboardText(i18nx.LocaleEnUS, "alert.pendingLongWait.title"); got != "Queued conversations are piling up" {
		t.Fatalf("dashboardText() = %q", got)
	}
	if got := dashboardText(i18nx.LocaleZhCN, "alert.pendingLongWait.title"); got != "待接入会话堆积" {
		t.Fatalf("dashboardText() = %q", got)
	}
}

func TestConversationStatusLabelUsesEnglishLocale(t *testing.T) {
	t.Parallel()

	if got := conversationStatusLabel(enums.IMConversationStatusPending, i18nx.LocaleEnUS); got != "Queued" {
		t.Fatalf("conversationStatusLabel() = %q", got)
	}
	if got := conversationStatusLabel(enums.IMConversationStatusPending, i18nx.LocaleZhCN); got != "待接入" {
		t.Fatalf("conversationStatusLabel() = %q", got)
	}
}
