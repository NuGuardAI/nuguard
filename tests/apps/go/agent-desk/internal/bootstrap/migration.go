package bootstrap

import (
	"agent-desk/internal/migration"
	"agent-desk/internal/models"

	"github.com/mlogclub/simple/sqls"
)

func InitMigrations() error {
	if err := sqls.DB().AutoMigrate(models.Models...); err != nil {
		return err
	}
	return migration.Migrate()
}
