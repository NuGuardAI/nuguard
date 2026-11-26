package builders

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/i18nx"
	"agent-desk/internal/pkg/utils"
	"regexp"
	"strings"
)

var (
	ticketAssignedNotificationPattern       = regexp.MustCompile(`^工单 (.+) 已指派给你$`)
	conversationAssignedNotificationPattern = regexp.MustCompile(`^会话 #([0-9]+) 已分配给你$`)
)

func BuildNotification(item *models.Notification) *response.NotificationResponse {
	return BuildNotificationWithLocale(item, i18nx.DefaultLocale)
}

func BuildNotificationWithLocale(item *models.Notification, locale string) *response.NotificationResponse {
	if item == nil {
		return nil
	}
	title, content := localizeNotificationText(item, locale)
	return &response.NotificationResponse{
		ID:               item.ID,
		RecipientUserID:  item.RecipientUserID,
		Title:            title,
		Content:          content,
		NotificationType: item.NotificationType,
		BizType:          item.BizType,
		BizID:            item.BizID,
		ActionURL:        item.ActionURL,
		ReadAt:           utils.FormatTimePtr(item.ReadAt),
		CreatedAt:        utils.FormatTime(item.CreatedAt),
	}
}

func BuildNotificationList(list []models.Notification) []response.NotificationResponse {
	return BuildNotificationListWithLocale(list, i18nx.DefaultLocale)
}

func BuildNotificationListWithLocale(list []models.Notification, locale string) []response.NotificationResponse {
	results := make([]response.NotificationResponse, 0, len(list))
	for i := range list {
		if item := BuildNotificationWithLocale(&list[i], locale); item != nil {
			results = append(results, *item)
		}
	}
	return results
}

func localizeNotificationText(item *models.Notification, locale string) (string, string) {
	title := item.Title
	content := item.Content
	if i18nx.NormalizeLocale(locale) != i18nx.LocaleEnUS {
		return title, content
	}
	switch strings.TrimSpace(item.NotificationType) {
	case "ticket_assigned":
		return localizeTicketAssignedNotification(title, content)
	case "conversation_assigned":
		return localizeConversationAssignedNotification(title, content)
	default:
		return title, content
	}
}

func localizeTicketAssignedNotification(title string, content string) (string, string) {
	lines := splitNotificationLines(content)
	if len(lines) == 0 {
		return localizeNotificationTitle(title), content
	}
	if matches := ticketAssignedNotificationPattern.FindStringSubmatch(lines[0]); len(matches) == 2 {
		lines[0] = i18nx.Getf(i18nx.LocaleEnUS, "notification.ticketAssigned.line", matches[1])
	}
	for i, line := range lines[1:] {
		if reason, ok := strings.CutPrefix(line, "指派原因: "); ok {
			lines[i+1] = i18nx.Getf(i18nx.LocaleEnUS, "notification.ticketAssigned.reason", reason)
		}
	}
	return localizeNotificationTitle(title), strings.Join(lines, "\n")
}

func localizeConversationAssignedNotification(title string, content string) (string, string) {
	lines := splitNotificationLines(content)
	if len(lines) == 0 {
		return localizeNotificationTitle(title), content
	}
	if matches := conversationAssignedNotificationPattern.FindStringSubmatch(lines[0]); len(matches) == 2 {
		lines[0] = i18nx.Getf(i18nx.LocaleEnUS, "notification.conversationAssigned.line", matches[1])
	}
	for i, line := range lines[1:] {
		if reason, ok := strings.CutPrefix(line, "分配原因: "); ok {
			lines[i+1] = i18nx.Getf(i18nx.LocaleEnUS, "notification.conversationAssigned.reason", reason)
			continue
		}
		if reason, ok := strings.CutPrefix(line, "转接原因: "); ok {
			lines[i+1] = i18nx.Getf(i18nx.LocaleEnUS, "notification.conversationTransferred.reason", reason)
		}
	}
	return localizeNotificationTitle(title), strings.Join(lines, "\n")
}

func localizeNotificationTitle(title string) string {
	switch strings.TrimSpace(title) {
	case "工单指派提醒":
		return i18nx.Getf(i18nx.LocaleEnUS, "notification.ticketAssigned.title")
	case "会话转接提醒":
		return i18nx.Getf(i18nx.LocaleEnUS, "notification.conversationTransferred.title")
	case "会话自动分配提醒":
		return i18nx.Getf(i18nx.LocaleEnUS, "notification.conversationAutoAssigned.title")
	case "会话分配提醒":
		return i18nx.Getf(i18nx.LocaleEnUS, "notification.conversationAssigned.title")
	default:
		return title
	}
}

func splitNotificationLines(content string) []string {
	normalized := strings.TrimSpace(content)
	if normalized == "" {
		return nil
	}
	return strings.Split(normalized, "\n")
}
