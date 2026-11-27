package services

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/enums"
	"testing"
	"time"

	"github.com/glebarez/sqlite"
	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
	"gorm.io/gorm/schema"
)

func TestPermissionServiceSyncBuiltinPermissions(t *testing.T) {
	db := setupPermissionServiceTestDB(t)
	now := time.Now()
	for _, spec := range constants.Roles {
		if err := db.Create(&models.Role{
			Name: spec.Name, Code: spec.Code, Status: enums.StatusOk, IsSystem: true, SortNo: spec.SortNo,
			AuditFields: models.AuditFields{CreatedAt: now, UpdatedAt: now},
		}).Error; err != nil {
			t.Fatalf("create role %s: %v", spec.Code, err)
		}
	}
	customPermission := &models.Permission{
		Name: "Custom permission", Code: "custom.keep", Type: "api", GroupName: "custom",
		Status: enums.StatusOk, AuditFields: models.AuditFields{CreatedAt: now, UpdatedAt: now},
	}
	if err := db.Create(customPermission).Error; err != nil {
		t.Fatalf("create custom permission: %v", err)
	}
	superAdmin := &models.Role{}
	if err := db.First(superAdmin, "code = ?", constants.RoleCodeSuperAdmin).Error; err != nil {
		t.Fatalf("find super admin role: %v", err)
	}
	if err := db.Create(&models.RolePermission{
		RoleID: superAdmin.ID, PermissionID: customPermission.ID,
		AuditFields: models.AuditFields{CreatedAt: now, UpdatedAt: now},
	}).Error; err != nil {
		t.Fatalf("create custom role permission: %v", err)
	}

	first, err := PermissionService.SyncBuiltinPermissions()
	if err != nil {
		t.Fatalf("first sync: %v", err)
	}
	if first.Created != len(constants.Permissions) || first.Updated != 0 {
		t.Fatalf("unexpected first sync result: %+v", first)
	}

	wantRolePermissions := 0
	for _, permissions := range constants.RolePermissions {
		wantRolePermissions += len(permissions)
	}
	if first.RolePermissionsAdded != wantRolePermissions {
		t.Fatalf("role permissions added=%d want=%d", first.RolePermissionsAdded, wantRolePermissions)
	}

	second, err := PermissionService.SyncBuiltinPermissions()
	if err != nil {
		t.Fatalf("second sync: %v", err)
	}
	if second.Created != 0 || second.Updated != len(constants.Permissions) || second.RolePermissionsAdded != 0 {
		t.Fatalf("sync is not idempotent: %+v", second)
	}

	var permissionCount int64
	if err := db.Model(&models.Permission{}).Count(&permissionCount).Error; err != nil {
		t.Fatalf("count permissions: %v", err)
	}
	if permissionCount != int64(len(constants.Permissions)+1) {
		t.Fatalf("permission count=%d want=%d", permissionCount, len(constants.Permissions)+1)
	}
	var customRolePermissionCount int64
	if err := db.Model(&models.RolePermission{}).
		Where("role_id = ? AND permission_id = ?", superAdmin.ID, customPermission.ID).
		Count(&customRolePermissionCount).Error; err != nil {
		t.Fatalf("count custom role permission: %v", err)
	}
	if customRolePermissionCount != 1 {
		t.Fatalf("custom role permission was removed")
	}
}

func setupPermissionServiceTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{
		NamingStrategy: schema.NamingStrategy{TablePrefix: "t_", SingularTable: true},
	})
	if err != nil {
		t.Fatalf("open sqlite db: %v", err)
	}
	if err := db.AutoMigrate(&models.Role{}, &models.Permission{}, &models.RolePermission{}); err != nil {
		t.Fatalf("migrate permission tables: %v", err)
	}
	sqls.SetDB(db)
	return db
}
