package dashboard

import (
	"agent-desk/internal/builders"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func UserAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionUserView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	cnd := params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "status"},
		params.QueryFilter{ParamName: "username", Op: params.Like},
		params.QueryFilter{ParamName: "nickname", Op: params.Like},
	).Desc("id")
	cnd.Where("status <> ?", enums.StatusDeleted)
	list, paging := services.UserService.FindPageByCnd(cnd)
	results := builders.BuildUserList(list, builders.UserBuildOptions{
		Roles:       true,
		Permissions: false,
	})
	httpx.WriteJSON(ctx, &web.PageResult{Results: results, Page: paging})
}

func UserAnyList_all(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionUserView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	cnd := params.NewSqlCnd(ctx,
		params.QueryFilter{ParamName: "status"},
		params.QueryFilter{ParamName: "username", Op: params.Like},
		params.QueryFilter{ParamName: "nickname", Op: params.Like},
	).Desc("id")
	cnd.Where("status <> ?", enums.StatusDeleted)

	list := services.UserService.Find(cnd)
	results := builders.BuildUserList(list, builders.UserBuildOptions{
		Roles:       true,
		Permissions: false,
	})
	httpx.WriteJSON(ctx, results)
}

func UserGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionUserView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	item := services.UserService.Get(id)
	if item == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0255"))
		return
	}
	httpx.WriteJSON(ctx, builders.BuildUserResponse(item, builders.UserBuildOptions{
		Roles:       true,
		Permissions: true,
	}))
}

func UserPostCreate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionUserCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.CreateUserRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	user, generatedPassword, err := services.UserService.CreateUser(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, &response.CreateUserResultResponse{
		User:     builders.BuildUserResponse(user, builders.UserBuildOptions{Roles: true, Permissions: true}),
		Password: generatedPassword,
	})
}

func UserPostUpdate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionUserUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.UpdateUserRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.UserService.UpdateUser(req, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func UserPostDelete(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionUserDelete)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.DeleteUserRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.UserService.DeleteUser(req.ID, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func UserPostUpdate_status(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionUserUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.UpdateUserStatusRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.UserService.UpdateStatus(req.ID, req.Status, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func UserPostReset_password(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionUserUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	var req struct {
		UserID int64 `json:"userId"`
	}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	password, err := services.UserService.ResetPassword(req.UserID, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, map[string]any{
		"password": password,
	})
}

func UserPostChange_password(ctx *gin.Context) {
	principal := services.AuthService.GetAuthPrincipal(ctx)
	if principal == nil {
		if _, err := services.AuthService.Authenticate(ctx); err != nil {
			httpx.WriteJSON(ctx, err)
			return
		}
		principal = services.AuthService.GetAuthPrincipal(ctx)
	}
	if principal == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.auth.expired"))
		return
	}

	req := request.ChangePasswordRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.UserService.ChangeOwnPassword(req.Password, principal); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func UserPostAssign_role(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionUserAssignRole)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.AssignRoleRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.UserService.AssignRoles(req.UserID, req.RoleIDs, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}
