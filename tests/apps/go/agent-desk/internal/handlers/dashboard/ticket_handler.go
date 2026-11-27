package dashboard

import (
	"agent-desk/internal/pkg/httpx"
	"strings"

	"agent-desk/internal/builders"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/sqls"
	"github.com/mlogclub/simple/web"
)

func TicketAnyList(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionTicketView)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	cnd := params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "status"},
		params.QueryFilter{ParamName: "currentAssigneeId"},
		params.QueryFilter{ParamName: "customerId"},
		params.QueryFilter{ParamName: "conversationId"},
		params.QueryFilter{ParamName: "source"},
		params.QueryFilter{ParamName: "channel"},
	).Desc("updated_at").Desc("id")
	if keyword, _ := params.Get(ctx, "keyword"); strings.TrimSpace(keyword) != "" {
		keyword = "%" + strings.TrimSpace(keyword) + "%"
		cnd.Where("ticket_no LIKE ? OR title LIKE ? OR description LIKE ?", keyword, keyword, keyword)
	}
	if tagID, _ := params.GetInt64(ctx, "tagId"); tagID > 0 {
		cnd.Where("id IN (SELECT ticket_id FROM t_ticket_tag WHERE tag_id = ?)", tagID)
	}
	if mine, _ := params.Get(ctx, "mine"); mine == "1" || strings.EqualFold(mine, "true") {
		cnd.Eq("current_assignee_id", operator.UserID)
	}
	if unassigned, _ := params.Get(ctx, "unassigned"); unassigned == "1" || strings.EqualFold(unassigned, "true") {
		cnd.Eq("current_assignee_id", 0)
	}
	if staleHoursValue, _ := params.Get(ctx, "staleHours"); strings.TrimSpace(staleHoursValue) != "" {
		staleHours, _ := params.GetInt(ctx, "staleHours")
		services.TicketService.ApplyStaleFilter(cnd, staleHours)
	}
	aggregate, err := services.TicketService.FindPageAggregateByCnd(cnd, operator.UserID)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, &web.PageResult{
		Results: builders.BuildTicketListWithContext(aggregate.List, &builders.TicketBuildContext{
			TagsByTicketID: aggregate.TagsByTicketID,
			Users:          aggregate.Users,
			Customers:      aggregate.Customers,
		}),
		Page: aggregate.Paging,
	})
}

func TicketAnySummary(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionTicketView)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	staleHours, _ := params.GetInt(ctx, "staleHours")
	httpx.WriteJSON(ctx, builders.BuildTicketSummary(services.TicketService.GetSummary(operator, staleHours)))
}

func TicketAnyView_list(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionTicketView)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildTicketViewList(services.TicketViewService.ListByUser(operator.UserID)))
}

func TicketPostSave_view(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionTicketView)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.SaveTicketViewRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.TicketViewService.Save(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildTicketView(item))
}

func TicketPostDelete_view(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionTicketView)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.DeleteTicketViewRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.TicketViewService.Delete(req.ID, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func TicketGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionTicketView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	detail, err := services.TicketService.GetDetail(id)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildTicketDetail(detail))
}

func TicketPostCreate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionTicketCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.CreateTicketRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.TicketService.CreateTicket(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildTicket(item))
}

func TicketPostCreate_from_conversation(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionTicketCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.CreateTicketFromConversationRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.TicketService.CreateFromConversation(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildTicket(item))
}

func TicketPostUpdate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionTicketUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.UpdateTicketRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.TicketService.UpdateTicket(req, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func TicketPostLink_customer(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionTicketUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.LinkTicketCustomerRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.TicketService.LinkCustomer(req.TicketID, req.CustomerID, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func TicketPostAssign(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionTicketAssign)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.AssignTicketRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.TicketService.AssignTicket(req, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func TicketPostChange_status(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionTicketChangeStatus)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.ChangeTicketStatusRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.TicketService.ChangeStatus(req, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func TicketAnyProgressList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionTicketView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	ticketID, _ := params.GetInt64(ctx, "ticketId")
	if ticketID <= 0 {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0178"))
		return
	}
	if services.TicketService.Get(ticketID) == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0178"))
		return
	}
	progresses := services.TicketProgressService.Find(sqls.NewCnd().Eq("ticket_id", ticketID).Asc("id"))
	httpx.WriteJSON(ctx, builders.BuildTicketProgressList(progresses))
}

func TicketPostProgressCreate(ctx *gin.Context) {
	ticketCreateProgress(ctx)
}

func ticketCreateProgress(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionTicketProgress)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.CreateTicketProgressRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.TicketService.AddProgress(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildTicketProgress(item))
}
