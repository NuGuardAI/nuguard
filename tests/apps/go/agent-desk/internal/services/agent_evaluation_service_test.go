package services

import (
	"context"
	"testing"

	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
)

func TestAgentEvaluationServiceValidatesAndCallsRunner(t *testing.T) {
	previous := AgentEvaluationRunHook
	t.Cleanup(func() { AgentEvaluationRunHook = previous })
	called := false
	AgentEvaluationRunHook = func(_ context.Context, req request.RunAgentEvaluationRequest) (*response.AgentEvaluationReportResponse, error) {
		called = true
		return &response.AgentEvaluationReportResponse{Total: len(req.Cases)}, nil
	}
	result, err := AgentEvaluationService.Run(context.Background(), request.RunAgentEvaluationRequest{AIAgentID: 1, Cases: []request.AgentEvaluationCase{{ID: "faq", Message: "hello"}}})
	if err != nil || !called || result.Total != 1 {
		t.Fatalf("result=%#v called=%t err=%v", result, called, err)
	}
	if _, err := AgentEvaluationService.Run(context.Background(), request.RunAgentEvaluationRequest{}); err == nil {
		t.Fatal("expected invalid request")
	}
}
