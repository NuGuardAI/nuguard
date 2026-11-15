package evaluation

import "reflect"

func ScoreToolCalls(expected []ExpectedTool, actual []ToolCall) float64 {
	if len(expected) == 0 {
		if len(actual) == 0 {
			return 1
		}
		return 0
	}
	var total float64
	for _, want := range expected {
		var found *ToolCall
		for index := range actual {
			if actual[index].Name == want.Name {
				found = &actual[index]
				break
			}
		}
		if found == nil {
			continue
		}
		total += .5
		arguments := make(map[string]any, len(want.Arguments))
		for key, value := range want.Arguments {
			arguments[key] = value
		}
		if reflect.DeepEqual(arguments, found.Arguments) {
			total += .5
		}
	}
	return total / float64(len(expected))
}

func ScoreCitations(expected []ExpectedCitation, actual []Citation) float64 {
	if len(expected) == 0 {
		if len(actual) == 0 {
			return 1
		}
		return 0
	}
	matches := 0
	for _, want := range expected {
		for _, got := range actual {
			if want.Title == got.Title && want.Locator == got.Locator {
				matches++
				break
			}
		}
	}
	return float64(matches) / float64(len(expected))
}
