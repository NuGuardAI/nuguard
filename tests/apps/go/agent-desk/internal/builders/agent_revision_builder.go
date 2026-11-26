package builders

import (
	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto/response"
)

func BuildAgentRevision(item *models.AgentRevision) response.AgentRevisionResponse {
	if item == nil {
		return response.AgentRevisionResponse{}
	}
	publishedAt := ""
	if item.PublishedAt != nil {
		publishedAt = item.PublishedAt.Format("2006-01-02 15:04:05")
	}
	return response.AgentRevisionResponse{
		ID: item.ID, AgentID: item.AgentID, Revision: item.Revision,
		Status: item.Status, DefinitionHash: item.DefinitionHash, PublishedAt: publishedAt,
		PublishedByID: item.PublishedByID, PublishedByName: item.PublishedByName,
	}
}

func BuildAgentRevisionList(items []models.AgentRevision) []response.AgentRevisionResponse {
	ret := make([]response.AgentRevisionResponse, 0, len(items))
	for i := range items {
		ret = append(ret, BuildAgentRevision(&items[i]))
	}
	return ret
}
