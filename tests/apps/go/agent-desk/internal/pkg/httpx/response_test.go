package httpx

import (
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/i18nx"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/mlogclub/simple/sqls"
	"github.com/mlogclub/simple/web"
)

func TestWriteJSONWrapsCommonResultTypes(t *testing.T) {
	gin.SetMode(gin.TestMode)

	tests := []struct {
		name       string
		input      any
		wantStatus int
		want       web.JsonResult
	}{
		{
			name:       "nil becomes success",
			input:      nil,
			wantStatus: http.StatusOK,
			want:       *web.JsonSuccess(),
		},
		{
			name:       "plain value becomes data",
			input:      map[string]any{"id": float64(1)},
			wantStatus: http.StatusOK,
			want:       *web.JsonData(map[string]any{"id": float64(1)}),
		},
		{
			name:       "error becomes json error",
			input:      errors.New("boom"),
			wantStatus: http.StatusOK,
			want:       *web.JsonError(errors.New("boom")),
		},
		{
			name:       "page data becomes page result",
			input:      PageData([]any{"a"}, &sqls.Paging{Page: 1, Limit: 20, Total: 1}),
			wantStatus: http.StatusOK,
			want:       *web.JsonPageData([]any{"a"}, &sqls.Paging{Page: 1, Limit: 20, Total: 1}),
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ctx, recorder := testContext()

			WriteJSON(ctx, tt.input)

			if recorder.Code != tt.wantStatus {
				t.Fatalf("status = %d, want %d", recorder.Code, tt.wantStatus)
			}
			var got web.JsonResult
			if err := json.Unmarshal(recorder.Body.Bytes(), &got); err != nil {
				t.Fatalf("decode response: %v", err)
			}
			if got.Success != tt.want.Success || got.ErrorCode != tt.want.ErrorCode || got.Message != tt.want.Message {
				t.Fatalf("result = %+v, want %+v", got, tt.want)
			}
		})
	}
}

func TestWriteJSONLocalizesI18nErrors(t *testing.T) {
	gin.SetMode(gin.TestMode)
	ctx, recorder := testContext()
	i18nx.SetLocale(ctx, i18nx.LocaleEnUS)

	WriteJSON(ctx, errorsx.InvalidParamI18n("error.e0116"))

	var got web.JsonResult
	if err := json.Unmarshal(recorder.Body.Bytes(), &got); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if got.Message != "Conversation not found." {
		t.Fatalf("message = %q, want %q", got.Message, "Conversation not found.")
	}
}

func TestWriteHttpStatusJSONUsesProvidedStatus(t *testing.T) {
	gin.SetMode(gin.TestMode)
	ctx, recorder := testContext()

	WriteHttpStatusJSON(ctx, http.StatusUnauthorized, web.JsonErrorMsg("unauthorized"))

	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusUnauthorized)
	}
}

func testContext() (*gin.Context, *httptest.ResponseRecorder) {
	recorder := httptest.NewRecorder()
	ctx, _ := gin.CreateTestContext(recorder)
	ctx.Request = httptest.NewRequest(http.MethodGet, "/", nil)
	return ctx, recorder
}
