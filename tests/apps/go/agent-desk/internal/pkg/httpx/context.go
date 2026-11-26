package httpx

import (
	"agent-desk/internal/pkg/httpx/params"
	"agent-desk/internal/pkg/openidentity"
	"agent-desk/internal/pkg/tracex"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/common/strs"
)

const (
	ctxKeyExternalUser = "externalUser"
)

func SetExternalUser(ctx *gin.Context, ext *openidentity.ExternalUser) {
	ctx.Set(ctxKeyExternalUser, ext)
}

func GetExternalUser(ctx *gin.Context) *openidentity.ExternalUser {
	v, _ := ctx.Get(ctxKeyExternalUser)
	ext, _ := v.(*openidentity.ExternalUser)
	return ext
}

func GetChannelID(ctx *gin.Context) string {
	if channelID := ctx.GetHeader("X-Channel-ID"); strs.IsNotBlank(channelID) {
		return channelID
	}
	if channelID, _ := params.Get(ctx, "channelId"); strs.IsNotBlank(channelID) {
		return channelID
	}
	return ""
}

func GetRequestID(ctx *gin.Context) string {
	if ctx == nil {
		return ""
	}
	if value, ok := ctx.Get(tracex.GinRequestIDKey); ok {
		if requestID, ok := value.(string); ok {
			return tracex.NormalizeRequestID(requestID)
		}
	}
	return tracex.NormalizeRequestID(ctx.GetHeader(tracex.RequestIDHeader))
}
