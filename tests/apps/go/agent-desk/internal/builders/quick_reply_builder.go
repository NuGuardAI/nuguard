package builders

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto/response"
)

func BuildQuickReplyResponse(item *models.QuickReply) *response.QuickReplyResponse {
	if item == nil {
		return nil
	}
	return &response.QuickReplyResponse{
		ID:        item.ID,
		GroupName: item.GroupName,
		Title:     item.Title,
		Content:   item.Content,
		Status:    item.Status,
		SortNo:    item.SortNo,
		CreatedBy: item.CreateUserID,
	}
}

func BuildQuickReplyResponses(list []models.QuickReply) []response.QuickReplyResponse {
	results := make([]response.QuickReplyResponse, 0, len(list))
	for _, item := range list {
		result := BuildQuickReplyResponse(&item)
		if result != nil {
			results = append(results, *result)
		}
	}
	return results
}
