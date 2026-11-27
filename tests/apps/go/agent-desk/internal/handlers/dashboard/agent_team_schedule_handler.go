package dashboard

import (
	"agent-desk/internal/builders"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/pkg/i18nx"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/web"
)

func AgentTeamScheduleAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentTeamScheduleView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	cnd := params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "teamId"},
	).Desc("start_at").Desc("id")
	list, paging := services.AgentTeamScheduleService.FindPageByCnd(cnd)
	results := make([]response.AgentTeamScheduleResponse, 0, len(list))
	for _, item := range list {
		results = append(results, buildAgentTeamScheduleResponse(&item))
	}
	httpx.WriteJSON(ctx, &web.PageResult{Results: results, Page: paging})
}

func AgentTeamScheduleAnyCalendar(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentTeamScheduleView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	startAt, _ := params.Get(ctx, "startAt")
	endAt, _ := params.Get(ctx, "endAt")
	teamID, _ := params.GetInt64(ctx, "teamId")
	list, err := services.AgentTeamScheduleService.FindCalendarSchedules(request.AgentTeamScheduleCalendarRequest{
		StartAt: startAt,
		EndAt:   endAt,
		TeamID:  teamID,
	})
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	results := make([]response.AgentTeamScheduleResponse, 0, len(list))
	for _, item := range list {
		results = append(results, buildAgentTeamScheduleResponse(&item))
	}
	httpx.WriteJSON(ctx, results)
}

func AgentTeamSchedulePostBatch_preview(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentTeamScheduleBatchGenerate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.AgentTeamScheduleBatchRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req.Locale = i18nx.Locale(ctx)
	ret, err := services.AgentTeamScheduleService.BatchPreview(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildAgentTeamScheduleBatchPreviewResponse(ret))
}

func AgentTeamSchedulePostBatch_generate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentTeamScheduleBatchGenerate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.AgentTeamScheduleBatchRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req.Locale = i18nx.Locale(ctx)
	ret, err := services.AgentTeamScheduleService.BatchGenerate(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildAgentTeamScheduleBatchGenerateResponse(ret))
}

func AgentTeamScheduleGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentTeamScheduleView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item := services.AgentTeamScheduleService.Get(id)
	if item == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0172"))
		return
	}
	httpx.WriteJSON(ctx, buildAgentTeamScheduleResponse(item))
}

func AgentTeamSchedulePostCreate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentTeamScheduleCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.CreateAgentTeamScheduleRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.AgentTeamScheduleService.CreateAgentTeamSchedule(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, buildAgentTeamScheduleResponse(item))
}

func AgentTeamSchedulePostUpdate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentTeamScheduleUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.UpdateAgentTeamScheduleRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AgentTeamScheduleService.UpdateAgentTeamSchedule(req, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func AgentTeamSchedulePostDelete(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionAgentTeamScheduleDelete); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.DeleteAgentTeamScheduleRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.AgentTeamScheduleService.DeleteAgentTeamSchedule(req.ID); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func buildAgentTeamScheduleResponse(item *models.AgentTeamSchedule) response.AgentTeamScheduleResponse {
	ret := response.AgentTeamScheduleResponse{
		ID:      item.ID,
		TeamID:  item.TeamID,
		StartAt: item.StartAt.Format("2006-01-02 15:04:05"),
		EndAt:   item.EndAt.Format("2006-01-02 15:04:05"),
		Remark:  item.Remark,
	}
	if team := services.AgentTeamService.Get(item.TeamID); team != nil {
		ret.TeamName = team.Name
	}
	return ret
}
