package channel

import (
	"agent-desk/cmd/testdata/seedlang"
	"agent-desk/cmd/testdata/seeds"
	"regexp"
	"testing"
)

var hanTextPattern = regexp.MustCompile(`\p{Han}`)

func TestEnglishChannelSeedDoesNotContainChineseText(t *testing.T) {
	for _, item := range seeds.ChannelSeeds(seedlang.English) {
		values := []string{item.Name, item.ConfigJSON, item.Remark}
		for _, value := range values {
			if hanTextPattern.MatchString(value) {
				t.Fatalf("english channel seed contains Chinese text: %q", value)
			}
		}
	}
}
