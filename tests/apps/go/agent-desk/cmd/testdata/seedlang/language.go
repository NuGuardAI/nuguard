package seedlang

import (
	"fmt"
	"strings"
)

type Language string

const (
	Chinese Language = "zh"
	English Language = "en"
)

func Parse(raw string) (Language, error) {
	normalized := strings.ToLower(strings.TrimSpace(raw))
	switch normalized {
	case "", "zh", "zh-cn", "chinese":
		return Chinese, nil
	case "en", "en-us", "english":
		return English, nil
	default:
		return "", fmt.Errorf("unsupported testdata language %q, supported: zh, en", raw)
	}
}
