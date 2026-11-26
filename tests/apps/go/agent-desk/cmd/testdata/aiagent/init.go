package aiagent

import (
	"agent-desk/cmd/testdata/seedlang"
	"agent-desk/cmd/testdata/seeds"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/utils"
	"agent-desk/internal/repositories"
	"fmt"
	"strings"
	"time"

	"github.com/mlogclub/simple/sqls"
)

type InitResult struct {
	Created int
	Updated int
}

// Init 初始化 AI Agent 测试数据
// 依赖于 AI Config 和 Knowledge Base 已初始化
func Init(lang seedlang.Language) (*InitResult, error) {
	result := &InitResult{}

	aiConfigID, err := getDefaultAIConfigID()
	if err != nil {
		return result, fmt.Errorf("get default ai config id failed: %w", err)
	}
	if aiConfigID == 0 {
		return result, fmt.Errorf("no default ai config found, please init ai config first")
	}

	knowledgeIDs, err := getDefaultKnowledgeIDs()
	if err != nil {
		return result, fmt.Errorf("get default knowledge ids failed: %w", err)
	}

	defaultTeamIDs := getDefaultTeamIDs()
	agentSeeds := seeds.AIAgentSeeds(lang)
	seedItems := buildModels(lang, aiConfigID, knowledgeIDs, defaultTeamIDs)
	for index, item := range seedItems {
		itemCopy := item
		if err := sqls.WithTransaction(func(ctx *sqls.TxContext) error {
			existing := repositories.AIAgentRepository.Take(ctx.Tx, "name = ?", itemCopy.Name)
			if existing == nil {
				for _, legacyName := range agentSeeds[index].LegacyNames {
					legacyName = strings.TrimSpace(legacyName)
					if legacyName == "" {
						continue
					}
					existing = repositories.AIAgentRepository.Take(ctx.Tx, "name = ?", legacyName)
					if existing != nil {
						break
					}
				}
			}
			if existing != nil {
				if err := ctx.Tx.Model(existing).Updates(seedUpdateColumns(itemCopy)).Error; err != nil {
					return err
				}
				result.Updated++
			} else {
				if err := ctx.Tx.Create(&itemCopy).Error; err != nil {
					return err
				}
				result.Created++
			}
			return nil
		}); err != nil {
			return nil, fmt.Errorf("upsert ai agent failed: %w", err)
		}
	}

	return result, nil
}

func buildModels(lang seedlang.Language, aiConfigID int64, knowledgeIDs []int64, defaultTeamIDs string) []models.AIAgent {
	now := time.Now()
	seedItems := seeds.AIAgentSeeds(lang)
	items := make([]models.AIAgent, 0, len(seedItems))
	for _, seed := range seedItems {
		items = append(items, models.AIAgent{
			Name:                seed.Name,
			Description:         seed.Description,
			Status:              enums.StatusOk,
			AIConfigID:          aiConfigID,
			ServiceMode:         seed.ServiceMode,
			SystemPrompt:        seed.SystemPrompt,
			WelcomeMessage:      seed.WelcomeMessage,
			ReplyTimeoutSeconds: seed.ReplyTimeoutSeconds,
			TeamIDs:             defaultTeamIDs,
			HandoffMode:         seed.HandoffMode,
			FallbackMode:        seed.FallbackMode,
			FallbackMessage:     seed.FallbackMessage,
			KnowledgeIDs:        utils.JoinInt64s(knowledgeIDs),
			SkillIDs:            "",
			AllowedMCPTools:     "",
			SortNo:              seed.SortNo,
			AuditFields: models.AuditFields{
				CreatedAt:      now,
				CreateUserID:   0,
				CreateUserName: "System",
				UpdatedAt:      now,
				UpdateUserID:   0,
				UpdateUserName: "System",
			},
		})
	}
	return items
}

func seedUpdateColumns(item models.AIAgent) map[string]any {
	return map[string]any{
		"name":                  item.Name,
		"description":           item.Description,
		"status":                item.Status,
		"ai_config_id":          item.AIConfigID,
		"service_mode":          item.ServiceMode,
		"system_prompt":         item.SystemPrompt,
		"welcome_message":       item.WelcomeMessage,
		"reply_timeout_seconds": item.ReplyTimeoutSeconds,
		"team_ids":              item.TeamIDs,
		"handoff_mode":          item.HandoffMode,
		"fallback_mode":         item.FallbackMode,
		"fallback_message":      item.FallbackMessage,
		"knowledge_ids":         item.KnowledgeIDs,
		"skill_ids":             item.SkillIDs,
		"allowed_mcp_tools":     item.AllowedMCPTools,
		"sort_no":               item.SortNo,
		"updated_at":            item.UpdatedAt,
		"update_user_id":        item.UpdateUserID,
		"update_user_name":      item.UpdateUserName,
	}
}

func getDefaultAIConfigID() (int64, error) {
	aiConfig := repositories.AIConfigRepository.Take(
		sqls.DB(),
		"model_type = ? AND status = ?",
		string(enums.AIModelTypeLLM),
		enums.StatusOk,
	)
	if aiConfig == nil {
		return 0, nil
	}
	return aiConfig.ID, nil
}

func getDefaultKnowledgeIDs() ([]int64, error) {
	knowledges := repositories.KnowledgeBaseRepository.Find(
		sqls.DB(),
		sqls.NewCnd().Where("status = ?", enums.StatusOk),
	)
	ids := make([]int64, 0, len(knowledges))
	for _, knowledge := range knowledges {
		ids = append(ids, knowledge.ID)
	}
	return ids, nil
}

func getDefaultTeamIDs() string {
	teams := repositories.AgentTeamRepository.Find(
		sqls.DB(),
		sqls.NewCnd().Where("status = ?", enums.StatusOk),
	)
	teamIDs := make([]int64, 0, len(teams))
	for _, team := range teams {
		teamIDs = append(teamIDs, team.ID)
	}
	return utils.JoinInt64s(teamIDs)
}
