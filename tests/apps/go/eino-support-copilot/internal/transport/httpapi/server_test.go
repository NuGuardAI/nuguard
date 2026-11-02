package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestHealthzReportsModeAndRequestID(t *testing.T) {
	handler := NewServer(Dependencies{Mode: "mock"})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/healthz", nil)

	handler.ServeHTTP(recorder, request)

	require.Equal(t, http.StatusOK, recorder.Code)
	assert.Equal(t, "application/json", recorder.Header().Get("Content-Type"))
	assert.NotEmpty(t, recorder.Header().Get("X-Request-ID"))
	var body map[string]string
	require.NoError(t, json.NewDecoder(recorder.Body).Decode(&body))
	assert.Equal(t, map[string]string{"status": "ok", "mode": "mock"}, body)
}

func TestRequestIDMiddlewarePreservesCallerID(t *testing.T) {
	handler := NewServer(Dependencies{Mode: "mock"})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	request.Header.Set("X-Request-ID", "resume-42")

	handler.ServeHTTP(recorder, request)

	assert.Equal(t, "resume-42", recorder.Header().Get("X-Request-ID"))
}

func TestUnknownRouteUsesStableJSONError(t *testing.T) {
	handler := NewServer(Dependencies{Mode: "mock"})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/missing", nil)

	handler.ServeHTTP(recorder, request)

	require.Equal(t, http.StatusNotFound, recorder.Code)
	var body ErrorResponse
	require.NoError(t, json.NewDecoder(recorder.Body).Decode(&body))
	assert.Equal(t, "NOT_FOUND", body.Code)
	assert.False(t, body.Retryable)
	assert.NotEmpty(t, body.RequestID)
}
