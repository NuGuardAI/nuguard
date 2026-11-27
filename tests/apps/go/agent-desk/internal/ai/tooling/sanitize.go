package tooling

import (
	"regexp"
	"strings"
)

const maxPreviewChars = 4000

var secretAssignmentPattern = regexp.MustCompile(`(?i)(?:"|')?(api[_-]?key|authorization|password|secret|token|cookie)(?:"|')?\s*([:=])\s*(?:"[^"]*"|'[^']*'|[^\s,;}]+)`)

// SanitizePreview keeps audit/model previews bounded and masks common secrets.
// It intentionally operates on plain text so it also covers malformed JSON.
func SanitizePreview(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ""
	}
	value = secretAssignmentPattern.ReplaceAllString(value, "$1$2***")
	runes := []rune(value)
	if len(runes) <= maxPreviewChars {
		return value
	}
	return strings.TrimSpace(string(runes[:maxPreviewChars])) + "\n[preview truncated]"
}
