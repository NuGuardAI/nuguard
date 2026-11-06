package evaluation

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestScoreToolCallsRequiresNameAndArguments(t *testing.T) {
	expected := []ExpectedTool{{Name: "get_payment_status", Arguments: map[string]string{"account_id": "acct_demo_pro"}}}
	actual := []ToolCall{{Name: "get_payment_status", Arguments: map[string]any{"account_id": "wrong"}}}

	score := ScoreToolCalls(expected, actual)

	assert.Equal(t, 0.5, score)
}

func TestScoreCitationsMatchesTitleAndLocator(t *testing.T) {
	expected := []ExpectedCitation{{Title: "Activation guide", Locator: "Timing"}}
	actual := []Citation{{Title: "Activation guide", Locator: "Wrong section"}}

	assert.Equal(t, 0.0, ScoreCitations(expected, actual))
}
