package errorsx

import (
	"agent-desk/internal/pkg/i18nx"

	"github.com/mlogclub/simple/web"
)

const (
	CodeInvalidParam         = 1000
	CodeBusinessError        = 2000
	CodeAuthUnauthorized     = 3000
	CodeAuthForbidden        = 3001
	CodeAuthInvalidToken     = 3002
	CodeAuthInvalidAccount   = 3003
	CodeAuthCredentialLocked = 3004
)

func InvalidParam(message string) error {
	return web.NewError(CodeInvalidParam, message)
}

func InvalidParamI18n(key string, args ...any) error {
	return NewI18nError(CodeInvalidParam, key, args...)
}

func BusinessError(code int, message string) error {
	return web.NewError(CodeBusinessError+code, message)
}

func BusinessErrorI18n(code int, key string, args ...any) error {
	return NewI18nError(CodeBusinessError+code, key, args...)
}

func Unauthorized(message string) error {
	return web.NewError(CodeAuthUnauthorized, message)
}

func UnauthorizedI18n(key string, args ...any) error {
	return NewI18nError(CodeAuthUnauthorized, key, args...)
}

func Forbidden(message string) error {
	return web.NewError(CodeAuthForbidden, message)
}

func ForbiddenI18n(key string, args ...any) error {
	return NewI18nError(CodeAuthForbidden, key, args...)
}

func InvalidToken(message string) error {
	return web.NewError(CodeAuthInvalidToken, message)
}

func InvalidTokenI18n(key string, args ...any) error {
	return NewI18nError(CodeAuthInvalidToken, key, args...)
}

func InvalidAccount(message string) error {
	return web.NewError(CodeAuthInvalidAccount, message)
}

func InvalidAccountI18n(key string, args ...any) error {
	return NewI18nError(CodeAuthInvalidAccount, key, args...)
}

func CredentialLocked(message string) error {
	return web.NewError(CodeAuthCredentialLocked, message)
}

func CredentialLockedI18n(key string, args ...any) error {
	return NewI18nError(CodeAuthCredentialLocked, key, args...)
}

type I18nError struct {
	Code int
	Key  string
	Args []any
}

func NewI18nError(code int, key string, args ...any) *I18nError {
	return &I18nError{Code: code, Key: key, Args: args}
}

func (e *I18nError) Error() string {
	if e == nil {
		return ""
	}
	return e.Message(i18nx.DefaultLocale)
}

func (e *I18nError) Unwrap() error {
	if e == nil {
		return nil
	}
	return web.NewError(e.Code, e.Error())
}

func (e *I18nError) Message(locale string) string {
	if e == nil {
		return ""
	}
	return i18nx.Getf(locale, e.Key, e.Args...)
}

func (e *I18nError) JsonResult(locale string) *web.JsonResult {
	if e == nil {
		return web.JsonSuccess()
	}
	return web.JsonErrorCode(e.Code, e.Message(locale))
}
