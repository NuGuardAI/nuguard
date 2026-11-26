package services

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/repositories"
	"fmt"
	"time"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/mlogclub/simple/sqls"
)

var PermissionService = newPermissionService()

func newPermissionService() *permissionService {
	return &permissionService{}
}

type permissionService struct {
}

func (s *permissionService) Get(id int64) *models.Permission {
	return repositories.PermissionRepository.Get(sqls.DB(), id)
}

func (s *permissionService) Take(where ...interface{}) *models.Permission {
	return repositories.PermissionRepository.Take(sqls.DB(), where...)
}

func (s *permissionService) Find(cnd *sqls.Cnd) []models.Permission {
	return repositories.PermissionRepository.Find(sqls.DB(), cnd)
}

func (s *permissionService) FindOne(cnd *sqls.Cnd) *models.Permission {
	return repositories.PermissionRepository.FindOne(sqls.DB(), cnd)
}

func (s *permissionService) FindPageByParams(params *params.QueryParams) (list []models.Permission, paging *sqls.Paging) {
	return repositories.PermissionRepository.FindPageByParams(sqls.DB(), params)
}

func (s *permissionService) FindPageByCnd(cnd *sqls.Cnd) (list []models.Permission, paging *sqls.Paging) {
	return repositories.PermissionRepository.FindPageByCnd(sqls.DB(), cnd)
}

func (s *permissionService) Count(cnd *sqls.Cnd) int64 {
	return repositories.PermissionRepository.Count(sqls.DB(), cnd)
}

func (s *permissionService) Create(t *models.Permission) error {
	return repositories.PermissionRepository.Create(sqls.DB(), t)
}

func (s *permissionService) Update(t *models.Permission) error {
	return repositories.PermissionRepository.Update(sqls.DB(), t)
}

func (s *permissionService) Updates(id int64, columns map[string]interface{}) error {
	return repositories.PermissionRepository.Updates(sqls.DB(), id, columns)
}

func (s *permissionService) UpdateColumn(id int64, name string, value interface{}) error {
	return repositories.PermissionRepository.UpdateColumn(sqls.DB(), id, name, value)
}

func (s *permissionService) Delete(id int64) {
	repositories.PermissionRepository.Delete(sqls.DB(), id)
}

func (s *permissionService) SyncBuiltinPermissions() (*response.PermissionSyncResponse, error) {
	result := &response.PermissionSyncResponse{}
	err := sqls.WithTransaction(func(ctx *sqls.TxContext) error {
		permissions := make(map[string]*models.Permission, len(constants.Permissions))
		now := time.Now()

		for _, spec := range constants.Permissions {
			permission := repositories.PermissionRepository.FindOne(ctx.Tx, sqls.NewCnd().Eq("code", spec.Code))
			if permission == nil {
				permission = &models.Permission{
					Name: spec.Name, Code: spec.Code, Type: spec.Type, GroupName: spec.GroupName,
					Method: spec.Method, APIPath: spec.APIPath, SortNo: spec.SortNo,
					Status: enums.StatusOk, IsBuiltin: true,
					AuditFields: systemPermissionAuditFields(now),
				}
				if err := repositories.PermissionRepository.Create(ctx.Tx, permission); err != nil {
					return err
				}
				result.Created++
			} else {
				if err := repositories.PermissionRepository.Updates(ctx.Tx, permission.ID, map[string]any{
					"name": spec.Name, "type": spec.Type, "group_name": spec.GroupName,
					"method": spec.Method, "api_path": spec.APIPath, "sort_no": spec.SortNo,
					"status": enums.StatusOk, "is_builtin": true,
					"update_user_id":   constants.SystemAuditUserID,
					"update_user_name": constants.SystemAuditUserName, "updated_at": now,
				}); err != nil {
					return err
				}
				permission = repositories.PermissionRepository.Get(ctx.Tx, permission.ID)
				result.Updated++
			}
			permissions[spec.Code] = permission
		}

		for roleCode, specs := range constants.RolePermissions {
			role := repositories.RoleRepository.GetByCode(ctx.Tx, roleCode)
			if role == nil {
				return fmt.Errorf("builtin role not found: %s", roleCode)
			}
			for _, spec := range specs {
				permission := permissions[spec.Code]
				if permission == nil {
					return fmt.Errorf("builtin permission not found: %s", spec.Code)
				}
				if repositories.RolePermissionRepository.FindOne(ctx.Tx, sqls.NewCnd().Eq("role_id", role.ID).Eq("permission_id", permission.ID)) != nil {
					continue
				}
				if err := repositories.RolePermissionRepository.Create(ctx.Tx, &models.RolePermission{
					RoleID: role.ID, PermissionID: permission.ID,
					AuditFields: systemPermissionAuditFields(now),
				}); err != nil {
					return err
				}
				result.RolePermissionsAdded++
			}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}

func systemPermissionAuditFields(now time.Time) models.AuditFields {
	return models.AuditFields{
		CreatedAt: now, CreateUserID: constants.SystemAuditUserID, CreateUserName: constants.SystemAuditUserName,
		UpdatedAt: now, UpdateUserID: constants.SystemAuditUserID, UpdateUserName: constants.SystemAuditUserName,
	}
}
