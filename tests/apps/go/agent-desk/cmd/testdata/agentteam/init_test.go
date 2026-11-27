package agentteam

import (
	"agent-desk/cmd/testdata/seedlang"
	"agent-desk/cmd/testdata/seeds"
	"regexp"
	"testing"
)

var hanTextPattern = regexp.MustCompile(`\p{Han}`)

func TestEnglishAgentTeamTextDoesNotContainChineseText(t *testing.T) {
	values := []string{seeds.AgentTeamName(seedlang.English)}
	for _, user := range seeds.AgentUsers(seedlang.English, "admin") {
		values = append(values, user.Nickname)
	}

	for _, value := range values {
		if hanTextPattern.MatchString(value) {
			t.Fatalf("english agent team seed contains Chinese text: %q", value)
		}
	}
}
