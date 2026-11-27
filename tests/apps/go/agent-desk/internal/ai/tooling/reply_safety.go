package tooling

import (
	"fmt"
	"strings"
	"unicode"
)

const maxCustomerReplyRunes = 8000

// NormalizeCustomerReply applies the final plain-text boundary before an AI
// response enters a customer conversation. It rejects likely credential
// assignments instead of masking them, because a masked secret is not useful
// customer-facing content.
func NormalizeCustomerReply(value string) (string, error) {
	value = strings.TrimSpace(value)
	if value == "" {
		return "", fmt.Errorf("ai reply is empty")
	}
	if secretAssignmentPattern.MatchString(value) {
		return "", fmt.Errorf("ai reply contains sensitive credential data")
	}
	var builder strings.Builder
	for _, r := range value {
		if unicode.IsControl(r) && r != '\n' && r != '\t' {
			continue
		}
		builder.WriteRune(r)
	}
	value = strings.TrimSpace(builder.String())
	for strings.Contains(value, "\n\n\n") {
		value = strings.ReplaceAll(value, "\n\n\n", "\n\n")
	}
	if value == "" {
		return "", fmt.Errorf("ai reply is empty")
	}
	if len([]rune(value)) > maxCustomerReplyRunes {
		return "", fmt.Errorf("ai reply exceeds maximum length")
	}
	return value, nil
}
