package skill

import (
	"agent-desk/cmd/testdata/seedlang"
	"agent-desk/cmd/testdata/seeds"
	"regexp"
	"testing"
)

var hanTextPattern = regexp.MustCompile(`\p{Han}`)

func TestEnglishSkillSeedDoesNotContainChineseText(t *testing.T) {
	for _, item := range seeds.SkillDefinitionSeeds(seedlang.English) {
		values := []string{item.Name, item.Description, item.Instruction, item.Examples, item.ToolWhitelist, item.Remark}
		for _, value := range values {
			if hanTextPattern.MatchString(value) {
				t.Fatalf("english skill seed contains Chinese text: %q", value)
			}
		}
	}
}
