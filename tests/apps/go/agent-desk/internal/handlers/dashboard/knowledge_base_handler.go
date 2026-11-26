package dashboard

import (
	"agent-desk/internal/pkg/httpx"
	"context"
	"log/slog"

	"agent-desk/internal/ai/rag"
	"agent-desk/internal/builders"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/repositories"
	"agent-desk/internal/services"

	"agent-desk/internal/pkg/httpx/params"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/sqls"
	"github.com/mlogclub/simple/web"
)

func KnowledgeBaseAnyList(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionKnowledgeBaseView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	cnd := params.NewPagedSqlCnd(ctx,
		params.QueryFilter{ParamName: "status"},
		params.QueryFilter{ParamName: "name", Op: params.Like},
	).Asc("sort_no").Desc("id")
	list, paging := services.KnowledgeBaseService.FindPageByCnd(cnd)
	results := make([]response.KnowledgeBaseResponse, 0, len(list))
	for _, item := range list {
		docCount := repositories.KnowledgeDocumentRepository.CountByKnowledgeBaseID(sqls.DB(), item.ID)
		faqCount := repositories.KnowledgeFAQRepository.CountByKnowledgeBaseID(sqls.DB(), item.ID)
		resp := builders.BuildKnowledgeBase(&item)
		resp.DocumentCount = docCount
		resp.FAQCount = faqCount
		results = append(results, resp)
	}
	httpx.WriteJSON(ctx, &web.PageResult{Results: results, Page: paging})
}

func KnowledgeBaseAnyList_all(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionKnowledgeBaseView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	list := services.KnowledgeBaseService.Find(params.NewSqlCnd(ctx,
		params.QueryFilter{ParamName: "status"},
	).Asc("sort_no").Desc("id"))
	results := make([]response.KnowledgeBaseResponse, 0, len(list))
	for _, item := range list {
		resp := builders.BuildKnowledgeBase(&item)
		results = append(results, resp)
	}
	httpx.WriteJSON(ctx, results)
}

func KnowledgeBaseGetBy(ctx *gin.Context) {
	id, ok := httpx.GetPathInt64(ctx, "id")
	if !ok {
		return
	}
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionKnowledgeBaseView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	item := services.KnowledgeBaseService.Get(id)
	if item == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0283"))
		return
	}
	resp := builders.BuildKnowledgeBase(item)
	resp.DocumentCount = repositories.KnowledgeDocumentRepository.CountByKnowledgeBaseID(sqls.DB(), item.ID)
	resp.FAQCount = repositories.KnowledgeFAQRepository.CountByKnowledgeBaseID(sqls.DB(), item.ID)
	httpx.WriteJSON(ctx, resp)
}

func KnowledgeBasePostCreate(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionKnowledgeBaseCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.CreateKnowledgeBaseRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.KnowledgeBaseService.CreateKnowledgeBase(req, user)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, builders.BuildKnowledgeBase(item))
}

func KnowledgeBasePostUpdate(ctx *gin.Context) {
	user, err := services.AuthService.RequirePermission(ctx, constants.PermissionKnowledgeBaseUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	req := request.UpdateKnowledgeBaseRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.KnowledgeBaseService.UpdateKnowledgeBase(req, user); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func KnowledgeBasePostDelete(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionKnowledgeBaseDelete); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	var req struct {
		ID int64 `json:"id"`
	}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.KnowledgeBaseService.DeleteKnowledgeBase(req.ID); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func KnowledgeBasePostUpdate_sort(ctx *gin.Context) {
	var ids []int64
	if err := params.ReadJSON(ctx, &ids); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.KnowledgeBaseService.UpdateSort(ids); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func KnowledgeBasePostRebuild_index(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionKnowledgeBaseUpdate); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	var req struct {
		ID int64 `json:"id"`
	}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}

	knowledgeBase := services.KnowledgeBaseService.Get(req.ID)
	if knowledgeBase == nil {
		httpx.WriteJSON(ctx, httpx.JsonErrorMsg(ctx, "error.e0283"))
		return
	}

	go func() {
		ctx := context.Background()
		if err := rag.Index.RebuildKnowledgeBaseIndex(ctx, req.ID); err != nil {
			slog.Error("Failed to rebuild knowledge base index", "knowledge_base_id", req.ID, "error", err)
		}
	}()

	httpx.WriteJSON(ctx, nil)
}
