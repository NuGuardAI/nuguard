package httpapi

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/evaluation"
	"github.com/stretchr/testify/require"
)

func TestEvaluationEndpointsRequireAgentAndStartAsynchronously(t *testing.T) {
	runID := uuid.New()
	service := &evaluationServiceStub{result: evaluation.RunResult{ID: runID.String(), Status: "queued", Total: 24}}
	for _, test := range []struct {
		role   string
		status int
	}{{role: "customer", status: http.StatusForbidden}, {role: "agent", status: http.StatusAccepted}} {
		handler := NewServer(Dependencies{Mode: "mock", Evaluations: service, Identity: &identityStub{id: uuid.New(), role: test.role}})
		recorder := httptest.NewRecorder()
		request := httptest.NewRequest(http.MethodPost, "/api/v1/evaluations", nil)
		request.Header.Set("Authorization", "Bearer "+"demo-user") // NuGuard fixture: was X-Demo-User

		handler.ServeHTTP(recorder, request)

		require.Equal(t, test.status, recorder.Code)
	}
}

type evaluationServiceStub struct {
	result evaluation.RunResult
	err    error
}

func (s *evaluationServiceStub) Start(context.Context) (evaluation.RunResult, error) {
	return s.result, s.err
}
func (s *evaluationServiceStub) Get(context.Context, uuid.UUID) (evaluation.RunResult, error) {
	return s.result, s.err
}
