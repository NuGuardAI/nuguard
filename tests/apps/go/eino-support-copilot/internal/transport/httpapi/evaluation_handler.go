package httpapi

import (
	"errors"
	"net/http"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/evaluation"
)

func startEvaluation(service EvaluationService, identity IdentityResolver) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if _, ok := requireDemoRole(w, r, identity, "agent"); !ok {
			return
		}
		if service == nil {
			WriteError(w, r, http.StatusServiceUnavailable, "EVALUATION_UNAVAILABLE", "evaluation service is unavailable", true)
			return
		}
		result, err := service.Start(r.Context())
		if err != nil {
			WriteError(w, r, http.StatusInternalServerError, "EVALUATION_START_FAILED", "evaluation could not be started", true)
			return
		}
		writeJSON(w, http.StatusAccepted, result)
	}
}

func getEvaluation(service EvaluationService, identity IdentityResolver) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if _, ok := requireDemoRole(w, r, identity, "agent"); !ok {
			return
		}
		if service == nil {
			WriteError(w, r, http.StatusServiceUnavailable, "EVALUATION_UNAVAILABLE", "evaluation service is unavailable", true)
			return
		}
		id, err := uuid.Parse(r.PathValue("id"))
		if err != nil {
			WriteError(w, r, http.StatusNotFound, "EVALUATION_NOT_FOUND", "evaluation not found", false)
			return
		}
		result, err := service.Get(r.Context(), id)
		if err != nil {
			if errors.Is(err, evaluation.ErrRunNotFound) {
				WriteError(w, r, http.StatusNotFound, "EVALUATION_NOT_FOUND", "evaluation not found", false)
				return
			}
			WriteError(w, r, http.StatusInternalServerError, "EVALUATION_GET_FAILED", "evaluation could not be loaded", true)
			return
		}
		writeJSON(w, http.StatusOK, result)
	}
}
