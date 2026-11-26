package dashboard

import (
	"agent-desk/internal/builders"
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/constants"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/httpx"
	"agent-desk/internal/pkg/httpx/params"
	"agent-desk/internal/services"

	"github.com/gin-gonic/gin"
)

func KnowledgeDirectoryGetList_all(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionKnowledgeBaseView); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	knowledgeBaseID, ok := params.GetInt64(ctx, "knowledgeBaseId")
	if !ok || knowledgeBaseID <= 0 {
		httpx.WriteJSON(ctx, errorsx.InvalidParamI18n("error.e0283"))
		return
	}
	list := services.KnowledgeDirectoryService.FindAllByKnowledgeBaseID(knowledgeBaseID)
	httpx.WriteJSON(ctx, buildKnowledgeDirectoryTreeResponses(list))
}

func KnowledgeDirectoryPostCreate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionKnowledgeBaseCreate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.CreateKnowledgeDirectoryRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	item, err := services.KnowledgeDirectoryService.CreateDirectory(req, operator)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	resp := builders.BuildKnowledgeDirectory(item)
	httpx.WriteJSON(ctx, &resp)
}

func KnowledgeDirectoryPostUpdate(ctx *gin.Context) {
	operator, err := services.AuthService.RequirePermission(ctx, constants.PermissionKnowledgeBaseUpdate)
	if err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.UpdateKnowledgeDirectoryRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.KnowledgeDirectoryService.UpdateDirectory(req, operator); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func KnowledgeDirectoryPostDelete(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionKnowledgeBaseDelete); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	req := request.DeleteKnowledgeDirectoryRequest{}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.KnowledgeDirectoryService.DeleteDirectory(req.ID); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func KnowledgeDirectoryPostUpdate_sort(ctx *gin.Context) {
	if _, err := services.AuthService.RequirePermission(ctx, constants.PermissionKnowledgeBaseUpdate); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	var req struct {
		KnowledgeBaseID int64   `json:"knowledgeBaseId"`
		ParentID        int64   `json:"parentId"`
		IDs             []int64 `json:"ids"`
	}
	if err := params.ReadJSON(ctx, &req); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	if err := services.KnowledgeDirectoryService.UpdateSort(req.KnowledgeBaseID, req.ParentID, req.IDs); err != nil {
		httpx.WriteJSON(ctx, err)
		return
	}
	httpx.WriteJSON(ctx, nil)
}

func buildKnowledgeDirectoryTreeResponses(list []models.KnowledgeDirectory) []response.KnowledgeDirectoryResponse {
	childrenByParent := make(map[int64][]models.KnowledgeDirectory, len(list))
	for _, item := range list {
		childrenByParent[item.ParentID] = append(childrenByParent[item.ParentID], item)
	}
	var build func(parentID int64) []response.KnowledgeDirectoryResponse
	build = func(parentID int64) []response.KnowledgeDirectoryResponse {
		items := childrenByParent[parentID]
		ret := make([]response.KnowledgeDirectoryResponse, 0, len(items))
		for _, item := range items {
			resp := builders.BuildKnowledgeDirectory(&item)
			resp.Children = build(item.ID)
			ret = append(ret, resp)
		}
		return ret
	}
	return build(0)
}
