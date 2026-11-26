package params

import (
	"agent-desk/internal/pkg/errorsx"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"reflect"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/go-playground/validator/v10"
	"github.com/gorilla/schema"
	"github.com/mlogclub/simple/common/dates"
	"github.com/mlogclub/simple/common/jsons"
	"github.com/mlogclub/simple/common/strs"
	"github.com/mlogclub/simple/sqls"
	"github.com/spf13/cast"
)

var (
	decoder  = schema.NewDecoder()
	validate = validator.New()
)

func init() {
	decoder.SetAliasTag("form")
	decoder.ZeroEmpty(true)
	decoder.IgnoreUnknownKeys(true)
}

func paramError(name string) error {
	return fmt.Errorf("unable to find param value '%s'", name)
}

func ReadForm(ctx *gin.Context, obj any) error {
	if ctx == nil {
		return errors.New("request context is nil")
	}
	if err := ctx.Request.ParseForm(); err != nil {
		return err
	}
	values := ctx.Request.Form
	if len(values) == 0 {
		if err := ctx.Request.ParseMultipartForm(32 << 20); err != nil && !errors.Is(err, http.ErrNotMultipart) {
			return err
		}
		values = ctx.Request.Form
	}
	if len(values) == 0 {
		return nil
	}
	if err := decoder.Decode(obj, values); err != nil {
		return err
	}
	return validateStruct(obj)
}

func ReadJSON(ctx *gin.Context, obj any) error {
	if ctx == nil {
		return errors.New("request context is nil")
	}
	if err := ctx.ShouldBindJSON(obj); err != nil {
		return err
	}
	return validateStruct(obj)
}

func validateStruct(obj any) error {
	if obj == nil {
		return nil
	}
	value := reflect.ValueOf(obj)
	for value.Kind() == reflect.Pointer {
		if value.IsNil() {
			return validate.Struct(obj)
		}
		value = value.Elem()
	}
	if value.Kind() != reflect.Struct {
		return nil
	}
	return validate.Struct(obj)
}

func Get(ctx *gin.Context, name string) (string, bool) {
	str := FormValue(ctx, name)
	return str, str != ""
}

func GetInt64(ctx *gin.Context, name string) (int64, bool) {
	str, ok := Get(ctx, name)
	if !ok {
		return 0, false
	}
	value, err := cast.ToInt64E(str)
	if err != nil {
		return 0, false
	}
	return value, true
}

func GetInt(ctx *gin.Context, name string) (int, bool) {
	str, ok := Get(ctx, name)
	if !ok {
		return 0, false
	}
	value, err := cast.ToIntE(str)
	if err != nil {
		return 0, false
	}
	return value, true
}

func GetBool(ctx *gin.Context, name string) (bool, bool) {
	str, ok := Get(ctx, name)
	if !ok {
		return false, false
	}
	value, err := cast.ToBoolE(str)
	if err != nil {
		return false, false
	}
	return value, true
}

func GetTime(ctx *gin.Context, name string) *time.Time {
	value, _ := Get(ctx, name)
	if strs.IsBlank(value) {
		return nil
	}
	layouts := []string{dates.FmtDateTime, dates.FmtDate, dates.FmtDateTimeNoSeconds}
	for _, layout := range layouts {
		if ret, err := dates.Parse(value, layout); err == nil {
			return &ret
		}
	}
	return nil
}

func GetInt64Arr(ctx *gin.Context, name string) []int64 {
	str, ok := Get(ctx, name)
	if !ok {
		return nil
	}
	str = strings.TrimSpace(str)
	if strings.HasPrefix(str, "[") && strings.HasSuffix(str, "]") {
		var ret []int64
		if err := jsons.Parse(str, &ret); err != nil {
			slog.Error(err.Error())
		}
		return ret
	}
	return StrSplitToInt64Arr(str)
}

func StrSplitToInt64Arr(str string) (ret []int64) {
	if strs.IsBlank(str) {
		return ret
	}
	for _, s := range strings.Split(str, ",") {
		i, err := cast.ToInt64E(strings.TrimSpace(s))
		if err == nil {
			ret = append(ret, i)
		}
	}
	return ret
}

func FormValue(ctx *gin.Context, name string) string {
	if ctx == nil {
		return ""
	}
	if value := ctx.PostForm(name); value != "" {
		return value
	}
	return ctx.Query(name)
}

func FormValueRequired(ctx *gin.Context, name string) (string, error) {
	str := FormValue(ctx, name)
	if len(str) == 0 {
		return "", errorsx.InvalidParamI18n("error.param.required", name)
	}
	return str, nil
}

func FormValueDefault(ctx *gin.Context, name, def string) string {
	if value := FormValue(ctx, name); value != "" {
		return value
	}
	return def
}

func FormValueInt(ctx *gin.Context, name string) (int, error) {
	str := FormValue(ctx, name)
	if str == "" {
		return 0, paramError(name)
	}
	return strconv.Atoi(str)
}

func FormValueIntDefault(ctx *gin.Context, name string, def int) int {
	if v, err := FormValueInt(ctx, name); err == nil {
		return v
	}
	return def
}

func FormValueInt64(ctx *gin.Context, name string) (int64, error) {
	str := FormValue(ctx, name)
	if str == "" {
		return 0, paramError(name)
	}
	return strconv.ParseInt(str, 10, 64)
}

func FormValueInt64Default(ctx *gin.Context, name string, def int64) int64 {
	if v, err := FormValueInt64(ctx, name); err == nil {
		return v
	}
	return def
}

func FormValueInt64Array(ctx *gin.Context, name string) []int64 {
	str := strings.TrimSpace(FormValue(ctx, name))
	if strings.HasPrefix(str, "[") && strings.HasSuffix(str, "]") {
		var ret []int64
		if err := jsons.Parse(str, &ret); err != nil {
			slog.Error(err.Error())
		}
		return ret
	}
	return StrSplitToInt64Arr(str)
}

func FormValueStringArray(ctx *gin.Context, name string) []string {
	str := FormValue(ctx, name)
	if len(str) == 0 {
		return nil
	}
	var ret []string
	for _, s := range strings.Split(str, ",") {
		s = strings.TrimSpace(s)
		if len(s) > 0 {
			ret = append(ret, s)
		}
	}
	return ret
}

func FormValueBool(ctx *gin.Context, name string) (bool, error) {
	str := FormValue(ctx, name)
	if str == "" {
		return false, paramError(name)
	}
	return strconv.ParseBool(str)
}

func FormValueBoolDefault(ctx *gin.Context, name string, def bool) bool {
	str := FormValue(ctx, name)
	if str == "" {
		return def
	}
	value, err := strconv.ParseBool(str)
	if err != nil {
		return def
	}
	return value
}

func FormDate(ctx *gin.Context, name string) *time.Time {
	return GetTime(ctx, name)
}

func GetPaging(ctx *gin.Context) *sqls.Paging {
	page := FormValueIntDefault(ctx, "page", 1)
	limit := FormValueIntDefault(ctx, "limit", 20)
	if page <= 0 {
		page = 1
	}
	if limit <= 0 {
		limit = 20
	}
	return &sqls.Paging{Page: page, Limit: limit}
}
