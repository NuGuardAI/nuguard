package skill

import (
	"agent-desk/cmd/testdata/seedlang"
	"agent-desk/cmd/testdata/seeds"
	"agent-desk/internal/models"
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

func Init(lang seedlang.Language) (*InitResult, error) {
	result := &InitResult{}
	seedItems := buildModels(lang)
	for _, item := range seedItems {
		itemCopy := item
		if err := sqls.WithTransaction(func(ctx *sqls.TxContext) error {
			existing := repositories.SkillDefinitionRepository.Take(ctx.Tx, "name = ?", strings.TrimSpace(itemCopy.Name))
			if existing != nil {
				if err := ctx.Tx.Model(existing).Updates(&itemCopy).Error; err != nil {
					return err
				}
				result.Updated++
				return nil
			}
			if err := ctx.Tx.Create(&itemCopy).Error; err != nil {
				return err
			}
			result.Created++
			return nil
		}); err != nil {
			return nil, fmt.Errorf("upsert skill failed: %w", err)
		}
	}
	return result, nil
}

func buildModels(lang seedlang.Language) []models.SkillDefinition {
	now := time.Now()
	seedItems := seeds.SkillDefinitionSeeds(lang)
	items := make([]models.SkillDefinition, 0, len(seedItems))
	for _, seed := range seedItems {
		items = append(items, models.SkillDefinition{
			Name:          seed.Name,
			Description:   seed.Description,
			Instruction:   seed.Instruction,
			Examples:      seed.Examples,
			ToolWhitelist: seed.ToolWhitelist,
			Status:        seed.Status,
			Remark:        seed.Remark,
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
