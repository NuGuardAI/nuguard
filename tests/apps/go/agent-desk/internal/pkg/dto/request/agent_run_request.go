package request

import "agent-desk/internal/pkg/enums"

type SaveAgentRunQualityFeedbackRequest struct {
	AgentRunID       int64                          `json:"agentRunId"`
	ResolutionStatus enums.AgentRunResolutionStatus `json:"resolutionStatus"`
	EvidenceStatus   enums.AgentRunEvidenceStatus   `json:"evidenceStatus"`
	Comment          string                         `json:"comment"`
}
