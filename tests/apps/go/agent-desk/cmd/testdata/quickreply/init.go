package quickreply

import (
	"agent-desk/cmd/testdata/seedlang"
	"agent-desk/cmd/testdata/seeds"
	"agent-desk/internal/models"
	"agent-desk/internal/repositories"
	"time"

	"github.com/mlogclub/simple/sqls"
)

func Init(lang seedlang.Language) error {
	seed := seeds.QuickReplySeeds(lang)
	return sqls.WithTransaction(func(ctx *sqls.TxContext) error {
		now := time.Now()
		for _, row := range seed {
			existing := repositories.QuickReplyRepository.Get(ctx.Tx, row.ID)
			if existing == nil {
				item := &models.QuickReply{
					ID:        row.ID,
					GroupName: row.GroupName,
					Title:     row.Title,
					Content:   row.Content,
					Status:    row.Status,
					SortNo:    row.SortNo,
					AuditFields: models.AuditFields{
						CreatedAt:      now,
						CreateUserID:   0,
						CreateUserName: "system",
						UpdatedAt:      now,
						UpdateUserID:   0,
						UpdateUserName: "system",
					},
				}
				if err := repositories.QuickReplyRepository.Create(ctx.Tx, item); err != nil {
					return err
				}
				continue
			}
			if err := repositories.QuickReplyRepository.Updates(ctx.Tx, row.ID, map[string]any{
				"group_name":       row.GroupName,
				"title":            row.Title,
				"content":          row.Content,
				"status":           row.Status,
				"sort_no":          row.SortNo,
				"updated_at":       now,
				"update_user_id":   0,
				"update_user_name": "system",
			}); err != nil {
				return err
			}
		}
		return nil
	})
}
