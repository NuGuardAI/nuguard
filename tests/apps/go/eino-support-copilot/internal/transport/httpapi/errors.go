package httpapi

import (
	"encoding/json"
	"net/http"
)

type ErrorResponse struct {
	Code      string `json:"code"`
	Message   string `json:"message"`
	Retryable bool   `json:"retryable"`
	RequestID string `json:"request_id"`
}

func WriteError(w http.ResponseWriter, r *http.Request, status int, code, message string, retryable bool) {
	writeJSON(w, status, ErrorResponse{
		Code:      code,
		Message:   message,
		Retryable: retryable,
		RequestID: RequestID(r.Context()),
	})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
