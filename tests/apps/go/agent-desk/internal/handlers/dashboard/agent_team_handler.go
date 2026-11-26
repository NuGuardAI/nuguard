package dashboard

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/sqls"
)

func AgentTeamAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentTeamView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	cnd := params.NewSqlCnd(ctx,
		params.QueryFilter{ParamName: "status"},
		params.QueryFilter{ParamName: "leaderUserId"},
		params.QueryFilter{ParamName: "name", Op: params.Like},
	).Desc("id")
	if _, ok := params.Get(ctx, "status"); !ok {
		cnd.Where("status <> ?", enums.StatusDeleted)
	}
	list := services.AgentTeamService.Find(cnd)
	results := make([]response.AgentTeamResponse, 0, len(list))
	for _, item := range list {
		results = append(results, buildAgentTeamResponse(&item))
	}
	httpx.WriteJSON(ctx, results)
}

func AgentTeamGetList_all(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentTeamView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	list := services.AgentTeamService.Find(sqls.NewCnd().Eq("status", enums.StatusOk))
	results := make([]response.AgentTeamResponse, 0, len(list))
	for _, item := range list {
		results = append(results, buildAgentTeamResponse(&item))
	}
	httpx.WriteJSON(ctx, results)
}

func AgentTeamGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentTeamView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item := services.AgentTeamService.Get(id)
	if item == nil || item.Status == enums.StatusDeleted {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0169"))
		return
	}
	httpx.WriteJSON(ctx, buildAgentTeamResponse(item))
}

func AgentTeamPostCreate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentTeamCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.CreateAgentTeamRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.AgentTeamService.CreateAgentTeam(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, buildAgentTeamResponse(item))
}

func AgentTeamPostUpdate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentTeamUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.UpdateAgentTeamRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AgentTeamService.UpdateAgentTeam(req, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AgentTeamPostDelete(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentTeamDelete)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.DeleteAgentTeamRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AgentTeamService.DeleteAgentTeam(req.ID, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func buildAgentTeamResponse(item *models.AgentTeam) response.AgentTeamResponse {
	ret := response.AgentTeamResponse{
		ID:           item.ID,
		Name:         item.Name,
		LeaderUserID: item.LeaderUserID,
		Status:       item.Status,
		Description:  item.Description,
		Remark:       item.Remark,
	}
	if user := services.UserService.Get(item.LeaderUserID); user != nil {
		ret.LeaderUsername = user.Username
		ret.LeaderNickname = user.Nickname
	}
	return ret
}
