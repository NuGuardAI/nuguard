package agent

import (
	"strings"

	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
)

type Guard struct {
	minimumConfidence float64
}

func NewGuard(minimumConfidence float64) *Guard {
	return &Guard{minimumConfidence: minimumConfidence}
}

func (g *Guard) Verify(draft domain.Draft, evidence EvidenceState) (domain.Draft, error) {
	if g.minimumConfidence < 0 || g.minimumConfidence > 1 {
		return domain.Draft{}, ErrInvalidGuardConfig
	}
	result := cloneDraft(draft)
	reasons := make([]string, 0, 6)

	if !validSchema(result) {
		reasons = append(reasons, ReasonSchemaInvalid)
	}
	if result.Priority == domain.PriorityP0 || result.Priority == domain.PriorityP1 {
		reasons = append(reasons, ReasonHighPriority)
	}
	if result.Confidence < g.minimumConfidence {
		reasons = append(reasons, ReasonLowConfidence)
	}
	if evidence.CitationCount == 0 {
		reasons = append(reasons, ReasonMissingCitation)
	}
	if evidence.FactCount == 0 {
		reasons = append(reasons, ReasonMissingFact)
	}
	if len(evidence.ToolFailures) > 0 {
		reasons = append(reasons, ReasonToolPartialFailure)
	}
	for _, existing := range draft.ReviewReasons {
		reasons = appendUnique(reasons, existing)
	}

	result.ReviewReasons = reasons
	result.RequiresReview = len(reasons) > 0
	if result.RequiresReview && !strings.Contains(result.Answer, "人工确认") {
		if strings.TrimSpace(result.Answer) != "" {
			result.Answer += " "
		}
		result.Answer += "需要人工确认后再发送。"
	}
	return result, nil
}

func validSchema(draft domain.Draft) bool {
	if strings.TrimSpace(draft.Intent) == "" || strings.TrimSpace(draft.Answer) == "" {
		return false
	}
	switch draft.Priority {
	case domain.PriorityP0, domain.PriorityP1, domain.PriorityP2, domain.PriorityP3:
	default:
		return false
	}
	return draft.Confidence >= 0 && draft.Confidence <= 1
}

func cloneDraft(draft domain.Draft) domain.Draft {
	result := draft
	result.Citations = append([]domain.Citation(nil), draft.Citations...)
	result.Facts = append([]domain.Fact(nil), draft.Facts...)
	result.ReviewReasons = append([]string(nil), draft.ReviewReasons...)
	return result
}

func appendUnique(values []string, value string) []string {
	for _, existing := range values {
		if existing == value {
			return values
		}
	}
	return append(values, value)
}
