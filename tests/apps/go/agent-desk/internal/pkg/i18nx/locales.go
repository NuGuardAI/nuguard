package i18nx

import (
	"embed"
	"fmt"
	"log/slog"
	"path"
	"strings"
	"sync"

	"github.com/gin-gonic/gin"
	"gopkg.in/yaml.v3"
)

//go:embed locales/*.yml
var localeFiles embed.FS

var (
	messagesOnce sync.Once
	messages     map[string]map[string]string
)

func T(ctx *gin.Context, key string, args ...any) string {
	if ctx == nil {
		return Getf(DefaultLocale, key, args...)
	}
	return Getf(Locale(ctx), key, args...)
}

func TLocale(locale string, key string, args ...any) string {
	return Getf(locale, key, args...)
}

func Get(key string) string {
	return Getf(DefaultLocale, key)
}

func Getf(locale string, key string, args ...any) string {
	format := lookup(NormalizeLocale(locale), key)
	if len(args) == 0 {
		return format
	}
	return fmt.Sprintf(format, args...)
}

func lookup(locale string, key string) string {
	loadMessages()
	if value := lookupLocale(locale, key); value != "" {
		return value
	}
	if locale != DefaultLocale {
		if value := lookupLocale(DefaultLocale, key); value != "" {
			return value
		}
	}
	slog.Warn("translation key not found", "key", key, "locale", locale)
	return key
}

func lookupLocale(locale string, key string) string {
	if values, ok := messages[locale]; ok {
		return values[key]
	}
	slog.Warn("locale not found", "locale", locale)
	return ""
}

func loadMessages() {
	messagesOnce.Do(func() {
		loaded := make(map[string]map[string]string)
		entries, err := localeFiles.ReadDir("locales")
		if err != nil {
			slog.Error("read locale files failed", "err", err)
			messages = loaded
			return
		}
		for _, entry := range entries {
			if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".yml") {
				continue
			}
			filePath := path.Join("locales", entry.Name())
			data, err := localeFiles.ReadFile(filePath)
			if err != nil {
				slog.Error("read locale file failed", "file", filePath, "err", err)
				continue
			}
			values := make(map[string]string)
			if err := yaml.Unmarshal(data, &values); err != nil {
				slog.Error("parse locale file failed", "file", filePath, "err", err)
				continue
			}
			locale := strings.TrimSuffix(entry.Name(), ".yml")
			loaded[NormalizeLocale(locale)] = values
		}
		messages = loaded
	})
}
