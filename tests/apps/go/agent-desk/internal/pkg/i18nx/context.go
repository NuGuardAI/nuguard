package i18nx

import "github.com/gin-gonic/gin"

const contextLocaleKey = "i18nx.locale"

func Locale(ctx *gin.Context) string {
	if ctx == nil {
		return DefaultLocale
	}
	value, ok := ctx.Get(contextLocaleKey)
	if !ok {
		return DefaultLocale
	}
	locale, ok := value.(string)
	if !ok {
		return DefaultLocale
	}
	return NormalizeLocale(locale)
}

func SetLocale(ctx *gin.Context, locale string) {
	if ctx == nil {
		return
	}
	ctx.Set(contextLocaleKey, NormalizeLocale(locale))
}
