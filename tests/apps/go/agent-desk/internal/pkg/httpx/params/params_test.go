package params

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

func newJSONContext(body string) *gin.Context {
	gin.SetMode(gin.TestMode)
	w := httptest.NewRecorder()
	ctx, _ := gin.CreateTestContext(w)
	ctx.Request = httptest.NewRequest(http.MethodPost, "/", strings.NewReader(body))
	ctx.Request.Header.Set("Content-Type", "application/json")
	return ctx
}

func TestReadJSONAcceptsRootArray(t *testing.T) {
	var ids []int64

	if err := ReadJSON(newJSONContext(`[3,4]`), &ids); err != nil {
		t.Fatalf("ReadJSON returned error: %v", err)
	}

	if len(ids) != 2 || ids[0] != 3 || ids[1] != 4 {
		t.Fatalf("expected ids [3 4], got %#v", ids)
	}
}
