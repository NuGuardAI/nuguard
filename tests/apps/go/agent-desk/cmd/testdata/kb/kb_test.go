package kb

import (
	"agent-desk/cmd/testdata/seedlang"
	"agent-desk/cmd/testdata/seeds"
	"regexp"
	"testing"
)

var hanTextPattern = regexp.MustCompile(`\p{Han}`)

func TestEnglishKnowledgeBaseTextDoesNotContainChineseText(t *testing.T) {
	seed := seeds.FAQKnowledgeBaseSeed(seedlang.English)
	for _, value := range []string{seed.Name, seed.Description, seed.Remark} {
		if hanTextPattern.MatchString(value) {
			t.Fatalf("english knowledge base text contains Chinese text: %q", value)
		}
	}
}

func TestEnglishKnowledgeFAQSeedsDoNotContainChineseText(t *testing.T) {
	seedItems := seeds.KnowledgeFAQSeeds(seedlang.English)
	if len(seedItems) == 0 {
		t.Fatal("english FAQ seeds are empty")
	}
	for _, seed := range seedItems {
		values := []string{seed.Question, seed.Answer, seed.Remark}
		values = append(values, seed.SimilarQuestions...)
		for _, value := range values {
			if hanTextPattern.MatchString(value) {
				t.Fatalf("english FAQ seed contains Chinese text: %q", value)
			}
		}
	}
}
