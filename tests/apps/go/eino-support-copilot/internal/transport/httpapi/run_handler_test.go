package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/runtrace"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestRunDetailPassesCustomerIdentityAndReturnsSafeRun(t *testing.T) {
	runID := uuid.New()
	service := &runServiceStub{run: domain.AgentRun{ID: runID, Status: domain.RunCompleted, Steps: []domain.AgentRunStep{}}}
	handler := NewServer(Dependencies{Mode: "mock", Runs: service, Identity: &identityStub{id: uuid.New(), role: "customer"}})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/runs/"+runID.String(), nil)
	request.Header.Set("Authorization", "Bearer "+"demo-customer") // NuGuard fixture: was X-Demo-User

	handler.ServeHTTP(recorder, request)

	require.Equal(t, http.StatusOK, recorder.Code)
	assert.Equal(t, "customer", service.getRole)
	var response domain.AgentRun
	require.NoError(t, json.NewDecoder(recorder.Body).Decode(&response))
	assert.Equal(t, runID, response.ID)
}

func TestReplayRunReturnsAcceptedAndMapsInvalidState(t *testing.T) {
	runID := uuid.New()
	for _, test := range []struct {
		name   string
		err    error
		status int
	}{
		{name: "accepted", status: http.StatusAccepted},
		{name: "running rejected", err: runtrace.ErrReplayNotAllowed, status: http.StatusConflict},
	} {
		t.Run(test.name, func(t *testing.T) {
			service := &runServiceStub{run: domain.AgentRun{ID: uuid.New(), ReplayedFromRunID: &runID}, replayErr: test.err}
			handler := NewServer(Dependencies{Mode: "mock", Runs: service, Identity: &identityStub{id: uuid.New(), role: "agent"}})
			recorder := httptest.NewRecorder()
			request := httptest.NewRequest(http.MethodPost, "/api/v1/runs/"+runID.String()+":replay", nil)
			request.Header.Set("Authorization", "Bearer "+"demo-agent") // NuGuard fixture: was X-Demo-User

			handler.ServeHTTP(recorder, request)

			require.Equal(t, test.status, recorder.Code)
		})
	}
}

type runServiceStub struct {
	run       domain.AgentRun
	getRole   string
	replayErr error
}

func (s *runServiceStub) Get(_ context.Context, _ uuid.UUID, _ uuid.UUID, role string) (domain.AgentRun, error) {
	s.getRole = role
	return s.run, nil
}

func (s *runServiceStub) Replay(context.Context, uuid.UUID, uuid.UUID, string) (domain.AgentRun, error) {
	return s.run, s.replayErr
}
