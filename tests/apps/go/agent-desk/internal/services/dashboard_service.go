package services

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/i18nx"
	"agent-desk/internal/repositories"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

var DashboardService = newDashboardService()

func newDashboardService() *dashboardService {
	return &dashboardService{}
}

type dashboardService struct {
}

func (s *dashboardService) GetOverview(rangeValue string, locale string) response.DashboardOverviewResponse {
	locale = i18nx.NormalizeLocale(locale)
	now := time.Now()
	normalizedRange, trendDays := normalizeDashboardRange(rangeValue)
	todayStart := startOfDay(now)
	trendStart := todayStart.AddDate(0, 0, -(trendDays - 1))
	db := sqls.DB()

	conversationTodayCount := repositories.DashboardRepository.CountConversations(db, func(tx *gorm.DB) *gorm.DB {
		return tx.Where("created_at >= ?", todayStart)
	})
	processingConversationCount := repositories.DashboardRepository.CountConversations(db, func(tx *gorm.DB) *gorm.DB {
		return tx.Where("status IN ?", []enums.IMConversationStatus{
			enums.IMConversationStatusAIServing,
			enums.IMConversationStatusActive,
		})
	})
	pendingConversationCount := repositories.DashboardRepository.CountConversations(db, func(tx *gorm.DB) *gorm.DB {
		return tx.Where("status = ?", enums.IMConversationStatusPending)
	})

	agentProfiles := repositories.DashboardRepository.ListEnabledAgentProfiles(db)
	agentTeams := repositories.DashboardRepository.ListEnabledAgentTeams(db)
	activeSchedules := repositories.DashboardRepository.ListActiveTeamSchedules(db, now, now)
	activeConversations := repositories.DashboardRepository.ListConversations(db, func(tx *gorm.DB) *gorm.DB {
		return tx.Where("status IN ?", []enums.IMConversationStatus{
			enums.IMConversationStatusAIServing,
			enums.IMConversationStatusPending,
			enums.IMConversationStatusActive,
		})
	})

	onlineAgents, busyAgents, offlineAgents, teamLoads := s.buildAgentStats(now, agentTeams, agentProfiles, activeSchedules, activeConversations)

	enabledAIAgentCount := repositories.DashboardRepository.CountAIAgents(db, func(tx *gorm.DB) *gorm.DB {
		return tx.Where("status = ?", enums.StatusOk)
	})
	enabledChannelCount := repositories.DashboardRepository.CountChannels(db, func(tx *gorm.DB) *gorm.DB {
		return tx.Where("status = ?", enums.StatusOk)
	})
	knowledgeRetrieveCount := repositories.DashboardRepository.CountKnowledgeRetrieveLogs(db, func(tx *gorm.DB) *gorm.DB {
		return tx.Where("created_at >= ?", todayStart)
	})
	knowledgeRetrieveFailCount := repositories.DashboardRepository.CountKnowledgeRetrieveLogs(db, func(tx *gorm.DB) *gorm.DB {
		return tx.Where("created_at >= ? AND answer_status IN ?", todayStart, []int{2, 3, 4})
	})
	agentRunFailCount := repositories.DashboardRepository.CountAgentRuns(db, func(tx *gorm.DB) *gorm.DB {
		return tx.Where("created_at >= ? AND status = ?", todayStart, "failed")
	})
	aiHandoffCount := repositories.DashboardRepository.CountConversations(db, func(tx *gorm.DB) *gorm.DB {
		return tx.Where("handoff_at >= ?", todayStart)
	})

	enabledAIAgents := repositories.DashboardRepository.ListAIAgents(db, func(tx *gorm.DB) *gorm.DB {
		return tx.Where("status = ?", enums.StatusOk)
	})
	alerts := s.buildAlerts(now, db, enabledAIAgents, agentTeams, activeSchedules, locale)

	return response.DashboardOverviewResponse{
		Range:       normalizedRange,
		GeneratedAt: now.Format("2006-01-02 15:04:05"),
		Summary: response.DashboardSummaryResponse{
			TodayNewConversations:        conversationTodayCount,
			ProcessingConversations:      processingConversationCount,
			PendingDispatchConversations: pendingConversationCount,
			OnlineAgents:                 onlineAgents,
			AIServiceRate:                calcAIServiceRate(activeConversations),
		},
		ConversationStats: response.DashboardSectionStatsResponse{
			StatusDistribution: buildConversationStatusDistribution(db, locale),
			Trend:              buildConversationTrend(db, trendStart),
		},
		AgentStats: response.DashboardAgentStatsResponse{
			OnlineAgents:  onlineAgents,
			BusyAgents:    busyAgents,
			OfflineAgents: offlineAgents,
			TeamLoads:     teamLoads,
		},
		AIStats: response.DashboardAIStatsResponse{
			EnabledAIAgents:                 enabledAIAgentCount,
			EnabledChannels:                 enabledChannelCount,
			TodayKnowledgeRetrieves:         knowledgeRetrieveCount,
			TodayKnowledgeRetrieveFailCount: knowledgeRetrieveFailCount,
			TodayKnowledgeRetrieveFailRate:  calcRate(knowledgeRetrieveFailCount, knowledgeRetrieveCount),
			TodayAgentRunFailCount:          agentRunFailCount,
			TodayAIHandoffCount:             aiHandoffCount,
		},
		Alerts:     alerts,
		QuickLinks: buildDashboardQuickLinks(locale),
	}
}

func (s *dashboardService) buildAgentStats(now time.Time, teams []models.AgentTeam, profiles []models.AgentProfile, schedules []models.AgentTeamSchedule, conversations []models.Conversation) (int64, int64, int64, []response.DashboardTeamLoadResponse) {
	const onlineWindow = 15 * time.Minute

	scheduledTeamIDs := make(map[int64]bool, len(schedules))
	for _, item := range schedules {
		scheduledTeamIDs[item.TeamID] = true
	}

	type teamCounter struct {
		totalAgents             int64
		onlineAgents            int64
		busyAgents              int64
		offlineAgents           int64
		waitingConversations    int64
		processingConversations int64
		maxConcurrentCapacity   int64
	}

	teamCounters := make(map[int64]*teamCounter, len(teams))
	for _, team := range teams {
		teamCounters[team.ID] = &teamCounter{}
	}

	var onlineAgents int64
	var busyAgents int64
	var offlineAgents int64

	for _, profile := range profiles {
		counter := teamCounters[profile.TeamID]
		if counter == nil {
			counter = &teamCounter{}
			teamCounters[profile.TeamID] = counter
		}
		counter.totalAgents++
		counter.maxConcurrentCapacity += int64(profile.MaxConcurrentCount)
		if profile.LastOnlineAt != nil && now.Sub(*profile.LastOnlineAt) <= onlineWindow {
			counter.onlineAgents++
			onlineAgents++
			if profile.ServiceStatus == enums.ServiceStatusBusy {
				counter.busyAgents++
				busyAgents++
			}
			continue
		}
		counter.offlineAgents++
		offlineAgents++
	}

	for _, item := range conversations {
		if item.CurrentTeamID <= 0 {
			continue
		}
		counter := teamCounters[item.CurrentTeamID]
		if counter == nil {
			counter = &teamCounter{}
			teamCounters[item.CurrentTeamID] = counter
		}
		switch item.Status {
		case enums.IMConversationStatusAIServing:
			counter.processingConversations++
		case enums.IMConversationStatusPending:
			counter.waitingConversations++
		case enums.IMConversationStatusActive:
			counter.processingConversations++
		}
	}

	teamLoads := make([]response.DashboardTeamLoadResponse, 0, len(teams))
	for _, team := range teams {
		counter := teamCounters[team.ID]
		if counter == nil {
			counter = &teamCounter{}
		}
		teamLoads = append(teamLoads, response.DashboardTeamLoadResponse{
			TeamID:                  team.ID,
			TeamName:                team.Name,
			TotalAgents:             counter.totalAgents,
			OnlineAgents:            counter.onlineAgents,
			BusyAgents:              counter.busyAgents,
			OfflineAgents:           counter.offlineAgents,
			WaitingConversations:    counter.waitingConversations,
			ProcessingConversations: counter.processingConversations,
			MaxConcurrentCapacity:   counter.maxConcurrentCapacity,
			LoadRate:                calcRate(counter.processingConversations, counter.maxConcurrentCapacity),
			HasScheduleNow:          scheduledTeamIDs[team.ID],
		})
	}

	sort.Slice(teamLoads, func(i, j int) bool {
		if teamLoads[i].WaitingConversations == teamLoads[j].WaitingConversations {
			if teamLoads[i].LoadRate == teamLoads[j].LoadRate {
				return teamLoads[i].TeamID < teamLoads[j].TeamID
			}
			return teamLoads[i].LoadRate > teamLoads[j].LoadRate
		}
		return teamLoads[i].WaitingConversations > teamLoads[j].WaitingConversations
	})

	return onlineAgents, busyAgents, offlineAgents, teamLoads
}

func (s *dashboardService) buildAlerts(now time.Time, db *gorm.DB, aiAgents []models.AIAgent, teams []models.AgentTeam, schedules []models.AgentTeamSchedule, locale string) []response.DashboardAlertResponse {
	alerts := make([]response.DashboardAlertResponse, 0, 4)
	pendingTimeout := now.Add(-10 * time.Minute)
	activeTimeout := now.Add(-30 * time.Minute)

	pendingLongWaitCount := repositories.DashboardRepository.CountConversations(db, func(tx *gorm.DB) *gorm.DB {
		return tx.Where("status = ? AND created_at < ?", enums.IMConversationStatusPending, pendingTimeout)
	})
	if pendingLongWaitCount > 0 {
		alerts = append(alerts, response.DashboardAlertResponse{
			ID:          "pending-long-wait",
			Level:       "warning",
			Title:       dashboardText(locale, "alert.pendingLongWait.title"),
			Description: dashboardText(locale, "alert.pendingLongWait.description"),
			Count:       pendingLongWaitCount,
			Link:        "/dashboard/conversations",
		})
	}

	staleProcessingCount := repositories.DashboardRepository.CountConversations(db, func(tx *gorm.DB) *gorm.DB {
		return tx.Where("status IN ? AND (last_message_at IS NULL OR last_message_at < ?)", []enums.IMConversationStatus{
			enums.IMConversationStatusAIServing,
			enums.IMConversationStatusActive,
		}, activeTimeout)
	})
	if staleProcessingCount > 0 {
		alerts = append(alerts, response.DashboardAlertResponse{
			ID:          "stale-processing",
			Level:       "warning",
			Title:       dashboardText(locale, "alert.staleProcessing.title"),
			Description: dashboardText(locale, "alert.staleProcessing.description"),
			Count:       staleProcessingCount,
			Link:        "/dashboard/conversations",
		})
	}

	scheduledTeamIDs := make(map[int64]bool, len(schedules))
	for _, item := range schedules {
		scheduledTeamIDs[item.TeamID] = true
	}
	var scheduleMissingCount int64
	for _, team := range teams {
		if !scheduledTeamIDs[team.ID] {
			scheduleMissingCount++
		}
	}
	if scheduleMissingCount > 0 {
		alerts = append(alerts, response.DashboardAlertResponse{
			ID:          "team-no-schedule",
			Level:       "info",
			Title:       dashboardText(locale, "alert.teamNoSchedule.title"),
			Description: dashboardText(locale, "alert.teamNoSchedule.description"),
			Count:       scheduleMissingCount,
			Link:        "/dashboard/agent-team-schedules",
		})
	}

	sort.Slice(alerts, func(i, j int) bool {
		if alerts[i].Count == alerts[j].Count {
			return alerts[i].ID < alerts[j].ID
		}
		return alerts[i].Count > alerts[j].Count
	})

	return alerts
}

func buildConversationStatusDistribution(db *gorm.DB, locale string) []response.DashboardStatusDistributionItem {
	ret := make([]response.DashboardStatusDistributionItem, 0, len(enums.IMConversationStatusValues))
	for _, status := range enums.IMConversationStatusValues {
		ret = append(ret, response.DashboardStatusDistributionItem{
			Status: int(status),
			Label:  conversationStatusLabel(status, locale),
			Count: repositories.DashboardRepository.CountConversations(db, func(tx *gorm.DB) *gorm.DB {
				return tx.Where("status = ?", status)
			}),
		})
	}
	return ret
}

func buildConversationTrend(db *gorm.DB, start time.Time) []response.DashboardTrendItem {
	created := repositories.DashboardRepository.ListConversations(db, func(tx *gorm.DB) *gorm.DB {
		return tx.Select("created_at").Where("created_at >= ?", start)
	})
	closed := repositories.DashboardRepository.ListConversations(db, func(tx *gorm.DB) *gorm.DB {
		return tx.Select("closed_at").Where("closed_at IS NOT NULL AND closed_at >= ?", start)
	})
	return buildTrendItems(start, created, closed, func(item models.Conversation) *time.Time {
		return &item.CreatedAt
	}, func(item models.Conversation) *time.Time {
		return item.ClosedAt
	})
}

func buildTrendItems(start time.Time, created []models.Conversation, closed []models.Conversation, createdAt func(models.Conversation) *time.Time, closedAt func(models.Conversation) *time.Time) []response.DashboardTrendItem {
	series := initTrendMap(start, time.Now())
	for _, item := range created {
		if ts := createdAt(item); ts != nil {
			series[ts.Format("2006-01-02")].NewCount++
		}
	}
	for _, item := range closed {
		if ts := closedAt(item); ts != nil {
			series[ts.Format("2006-01-02")].ClosedCount++
		}
	}
	return flattenTrendMap(series)
}

func initTrendMap(start, end time.Time) map[string]*response.DashboardTrendItem {
	series := make(map[string]*response.DashboardTrendItem)
	for current := startOfDay(start); !current.After(end); current = current.AddDate(0, 0, 1) {
		key := current.Format("2006-01-02")
		series[key] = &response.DashboardTrendItem{Date: key}
	}
	return series
}

func flattenTrendMap(series map[string]*response.DashboardTrendItem) []response.DashboardTrendItem {
	keys := make([]string, 0, len(series))
	for key := range series {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	ret := make([]response.DashboardTrendItem, 0, len(keys))
	for _, key := range keys {
		ret = append(ret, *series[key])
	}
	return ret
}

func buildDashboardQuickLinks(locale string) []response.DashboardQuickLinkResponse {
	return []response.DashboardQuickLinkResponse{
		{Title: dashboardText(locale, "quick.conversations.title"), Description: dashboardText(locale, "quick.conversations.description"), Link: "/dashboard/conversations"},
		{Title: dashboardText(locale, "quick.agents.title"), Description: dashboardText(locale, "quick.agents.description"), Link: "/dashboard/agents"},
		{Title: dashboardText(locale, "quick.knowledge.title"), Description: dashboardText(locale, "quick.knowledge.description"), Link: "/dashboard/knowledge"},
		{Title: dashboardText(locale, "quick.aiAgents.title"), Description: dashboardText(locale, "quick.aiAgents.description"), Link: "/dashboard/ai-agents"},
		{Title: dashboardText(locale, "quick.channels.title"), Description: dashboardText(locale, "quick.channels.description"), Link: "/dashboard/channels"},
	}
}

func normalizeDashboardRange(value string) (string, int) {
	switch value {
	case "30d":
		return "30d", 30
	case "today":
		return "today", 1
	default:
		return "7d", 7
	}
}

func startOfDay(value time.Time) time.Time {
	return time.Date(value.Year(), value.Month(), value.Day(), 0, 0, 0, 0, value.Location())
}

func calcRate(numerator, denominator int64) float64 {
	if denominator <= 0 {
		return 0
	}
	ratio := float64(numerator) / float64(denominator) * 100
	return float64(int(ratio*10+0.5)) / 10
}

func calcAIServiceRate(conversations []models.Conversation) float64 {
	var aiCount int64
	var total int64
	for _, item := range conversations {
		total++
		if item.ServiceMode == enums.IMConversationServiceModeAIOnly || item.ServiceMode == enums.IMConversationServiceModeAIFirst {
			aiCount++
		}
	}
	return calcRate(aiCount, total)
}

func labelOrDefault(value, fallback string) string {
	if strings.TrimSpace(value) != "" {
		return value
	}
	return fallback
}

func conversationStatusLabel(status enums.IMConversationStatus, locale string) string {
	if i18nx.NormalizeLocale(locale) == i18nx.LocaleEnUS {
		switch status {
		case enums.IMConversationStatusAIServing:
			return "AI active"
		case enums.IMConversationStatusPending:
			return "Queued"
		case enums.IMConversationStatusActive:
			return "In progress"
		case enums.IMConversationStatusClosed:
			return "Closed"
		default:
			return fmt.Sprintf("Status %d", status)
		}
	}
	return labelOrDefault(enums.GetIMConversationStatusLabel(status), fmt.Sprintf("状态 %d", status))
}

func dashboardText(locale string, key string) string {
	if i18nx.NormalizeLocale(locale) == i18nx.LocaleEnUS {
		if value, ok := dashboardEnUS[key]; ok {
			return value
		}
	}
	if value, ok := dashboardZhCN[key]; ok {
		return value
	}
	return key
}

var dashboardZhCN = map[string]string{
	"alert.pendingLongWait.title":       "待接入会话堆积",
	"alert.pendingLongWait.description": "存在超过 10 分钟仍未接入的会话，建议优先处理分配。",
	"alert.staleProcessing.title":       "处理中会话长时间无响应",
	"alert.staleProcessing.description": "部分处理中会话已超过 30 分钟没有最新消息，需要确认跟进状态。",
	"alert.teamNoSchedule.title":        "客服组当前无生效排班",
	"alert.teamNoSchedule.description":  "部分启用中的客服组当前没有生效排班，可能影响自动分配。",
	"alert.aiNoKnowledge.title":         "AI Agent 未绑定知识库",
	"alert.aiNoKnowledge.description":   "部分启用中的 AI Agent 尚未绑定知识库，回答质量可能不稳定。",
	"quick.conversations.title":         "会话管理",
	"quick.conversations.description":   "查看待接入与处理中会话",
	"quick.agents.title":                "客服档案",
	"quick.agents.description":          "查看客服状态与分组配置",
	"quick.knowledge.title":             "知识库",
	"quick.knowledge.description":       "维护文档与查看检索日志",
	"quick.aiAgents.title":              "AI Agent",
	"quick.aiAgents.description":        "配置 AI 接待策略与知识绑定",
	"quick.channels.title":              "接入渠道",
	"quick.channels.description":        "管理接入渠道与默认 Agent",
}

var dashboardEnUS = map[string]string{
	"alert.pendingLongWait.title":       "Queued conversations are piling up",
	"alert.pendingLongWait.description": "Some conversations have been waiting for more than 10 minutes. Prioritize assignment.",
	"alert.staleProcessing.title":       "Active conversations need attention",
	"alert.staleProcessing.description": "Some active conversations have had no new messages for over 30 minutes. Check their follow-up status.",
	"alert.teamNoSchedule.title":        "Agent teams have no active schedule",
	"alert.teamNoSchedule.description":  "Some enabled agent teams do not have an active schedule right now, which may affect automatic assignment.",
	"alert.aiNoKnowledge.title":         "AI Agents are missing knowledge bases",
	"alert.aiNoKnowledge.description":   "Some enabled AI Agents are not linked to a knowledge base yet, which may reduce answer quality.",
	"quick.conversations.title":         "Conversations",
	"quick.conversations.description":   "Review queued and active conversations",
	"quick.agents.title":                "Agents",
	"quick.agents.description":          "Check agent status and team setup",
	"quick.knowledge.title":             "Knowledge base",
	"quick.knowledge.description":       "Manage documents and review retrieval logs",
	"quick.aiAgents.title":              "AI Agents",
	"quick.aiAgents.description":        "Configure AI service policies and knowledge bindings",
	"quick.channels.title":              "Channels",
	"quick.channels.description":        "Manage channels and default agents",
}
