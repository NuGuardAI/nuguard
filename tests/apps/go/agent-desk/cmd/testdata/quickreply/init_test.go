package quickreply

import (
	"agent-desk/cmd/testdata/seedlang"
	"agent-desk/cmd/testdata/seeds"
	"regexp"
	"testing"
)

var hanTextPattern = regexp.MustCompile(`\p{Han}`)

func TestEnglishSeedItemsMatchChineseCount(t *testing.T) {
	englishItems := seeds.QuickReplySeeds(seedlang.English)
	chineseItems := seeds.QuickReplySeeds(seedlang.Chinese)

	if len(englishItems) != len(chineseItems) {
		t.Fatalf("english seed count = %d, want %d", len(englishItems), len(chineseItems))
	}
}

func TestEnglishSeedItemsDoNotContainChineseText(t *testing.T) {
	for _, item := range seeds.QuickReplySeeds(seedlang.English) {
		for _, value := range []string{item.GroupName, item.Title, item.Content} {
			if hanTextPattern.MatchString(value) {
				t.Fatalf("english quick reply %d contains Chinese text: %q", item.ID, value)
			}
		}
	}
}
