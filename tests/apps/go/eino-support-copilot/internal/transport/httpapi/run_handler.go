package httpapi

import (
	"errors"
	"net/http"
	"strings"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/runtrace"
)

func getRun(service RunService, identity IdentityResolver) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		actorID, role, ok := requireDemoIdentity(w, r, identity)
		if !ok {
			return
		}
		if service == nil {
			WriteError(w, r, http.StatusServiceUnavailable, "RUN_SERVICE_UNAVAILABLE", "run service is unavailable", true)
			return
		}
		runID, err := uuid.Parse(r.PathValue("id"))
		if err != nil {
			WriteError(w, r, http.StatusNotFound, "RUN_NOT_FOUND", "run not found", false)
			return
		}
		run, err := service.Get(r.Context(), runID, actorID, role)
		if err != nil {
			writeRunError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, run)
	}
}

func replayRun(service RunService, identity IdentityResolver) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		actorID, role, ok := requireDemoIdentity(w, r, identity)
		if !ok {
			return
		}
		if service == nil {
			WriteError(w, r, http.StatusServiceUnavailable, "RUN_SERVICE_UNAVAILABLE", "run service is unavailable", true)
			return
		}
		raw := r.PathValue("action")
		if !strings.HasSuffix(raw, ":replay") {
			WriteError(w, r, http.StatusNotFound, "NOT_FOUND", "route not found", false)
			return
		}
		runID, err := uuid.Parse(strings.TrimSuffix(raw, ":replay"))
		if err != nil {
			WriteError(w, r, http.StatusNotFound, "RUN_NOT_FOUND", "run not found", false)
			return
		}
		created, err := service.Replay(r.Context(), runID, actorID, role)
		if err != nil {
			writeRunError(w, r, err)
			return
		}
		writeJSON(w, http.StatusAccepted, created)
	}
}

func writeRunError(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, runtrace.ErrNotFound):
		WriteError(w, r, http.StatusNotFound, "RUN_NOT_FOUND", "run not found", false)
	case errors.Is(err, runtrace.ErrForbidden):
		WriteError(w, r, http.StatusForbidden, "FORBIDDEN", "run access is forbidden", false)
	case errors.Is(err, runtrace.ErrReplayNotAllowed):
		WriteError(w, r, http.StatusConflict, "RUN_REPLAY_NOT_ALLOWED", "run cannot be replayed in its current state", false)
	case errors.Is(err, runtrace.ErrReplayUnavailable):
		WriteError(w, r, http.StatusServiceUnavailable, "RUN_REPLAY_UNAVAILABLE", "run replay is unavailable", true)
	default:
		WriteError(w, r, http.StatusInternalServerError, "RUN_OPERATION_FAILED", "run operation failed", true)
	}
}
