package tag

import (
	"agent-desk/cmd/testdata/seedlang"
	"agent-desk/cmd/testdata/seeds"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"
	"time"

	"github.com/mlogclub/simple/sqls"
)

func Init(lang seedlang.Language) error {
	seed := seeds.TagSeeds(lang)
	return sqls.WithTransaction(func(ctx *sqls.TxContext) error {
		now := time.Now()
		for _, row := range seed {
			existing := repositories.TagRepository.Get(ctx.Tx, row.ID)
			if existing == nil {
				tag := &models.Tag{
					ID:       row.ID,
					ParentID: row.ParentID,
					Name:     row.Name,
					Remark:   "",
					SortNo:   row.SortNo,
					Status:   enums.StatusOk,
					AuditFields: models.AuditFields{
						CreatedAt:      now,
						CreateUserID:   0,
						CreateUserName: "",
						UpdatedAt:      now,
						UpdateUserID:   0,
						UpdateUserName: "",
					},
				}
				if err := repositories.TagRepository.Create(ctx.Tx, tag); err != nil {
					return err
				}
				continue
			}
			if err := repositories.TagRepository.Updates(ctx.Tx, row.ID, map[string]any{
				"parent_id":        row.ParentID,
				"name":             row.Name,
				"remark":           "",
				"sort_no":          row.SortNo,
				"status":           enums.StatusOk,
				"updated_at":       now,
				"update_user_id":   0,
				"update_user_name": "",
			}); err != nil {
				return err
			}
		}
		return nil
	})
}
