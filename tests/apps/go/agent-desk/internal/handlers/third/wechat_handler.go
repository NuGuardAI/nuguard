package third

import (
	"agent-desk/internal/pkg/httpx/params"
	"agent-desk/internal/wxwork"
	"io"
	"net/http"

	"github.com/gin-gonic/gin"
	"github.com/silenceper/wechat/v2/work/kf"
)

// GetCallback GET请求用于校验回调是否配置正确
func WechatGetCallback(ctx *gin.Context) {
	cli, err := wxwork.GetWorkCli().GetKF()
	if err != nil {
		ctx.AbortWithError(http.StatusInternalServerError, err)
		return
	}
	options := kf.SignatureOptions{}
	if err := params.ReadForm(ctx, &options); err != nil {
		ctx.AbortWithError(http.StatusUnauthorized, err)
		return
	}
	// 调用VerifyURL方法校验当前请求，如果合法则把解密后的内容作为响应返回给微信服务器
	echo, err := cli.VerifyURL(options)
	if err == nil {
		ctx.String(http.StatusOK, echo)
	} else {
		ctx.AbortWithError(http.StatusUnauthorized, err)
	}
}

// PostCallback POST请求用于接收回调
func WechatPostCallback(ctx *gin.Context) {
	cli, err := wxwork.GetWorkCli().GetKF()
	if err != nil {
		ctx.AbortWithError(http.StatusInternalServerError, err)
		return
	}
	var (
		message kf.CallbackMessage
		body    []byte
	)
	// 读取原始消息内容
	body, err = io.ReadAll(ctx.Request.Body)
	if err != nil {
		ctx.AbortWithError(http.StatusInternalServerError, err)
		return
	}
	// 解析原始数据
	message, err = cli.GetCallbackMessage(body)
	if err != nil {
		ctx.AbortWithError(http.StatusInternalServerError, err)
		return
	}

	if err := wxwork.ConsumeCallback(message); err != nil {
		ctx.AbortWithError(http.StatusInternalServerError, err)
		return
	}

	ctx.String(http.StatusOK, "ok")
}
