package runtime

import (
	"context"

	applicationruntime "agent-desk/internal/ai/application/runtime"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/dto/response"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	svc "agent-desk/internal/services"
)

func init() {
	svc.AgentEvaluationRunHook = RunAgentEvaluation
}

func RunAgentEvaluation(ctx context.Context, req request.RunAgentEvaluationRequest) (*response.AgentEvaluationReportResponse, error) {
	agent := svc.AIAgentService.Get(req.AIAgentID)
	if agent == nil || agent.Status != enums.StatusOk {
		return nil, errorsx.InvalidParamI18n("error.e0007")
	}
	config := svc.AIConfigService.Get(agent.AIConfigID)
	if config == nil {
		return nil, errorsx.InvalidParamI18n("error.e0008")
	}
	cases := make([]applicationruntime.OfflineEvaluationCase, 0, len(req.Cases))
	for _, item := range req.Cases {
		cases = append(cases, applicationruntime.OfflineEvaluationCase{ID: item.ID, Category: item.Category, Message: item.Message, History: item.History, Expect: item.Expect})
	}
	report, err := applicationruntime.NewService().RunOfflineEvaluation(ctx, *agent, *config, cases)
	if err != nil {
		return nil, err
	}
	csv, err := report.CSV()
	if err != nil {
		return nil, err
	}
	ret := &response.AgentEvaluationReportResponse{Total: report.Total, Passed: report.Passed, CSV: csv, Results: make([]response.AgentEvaluationResultResponse, 0, len(report.Results))}
	for _, item := range report.Results {
		ret.Results = append(ret.Results, response.AgentEvaluationResultResponse{CaseID: item.CaseID, Category: item.Category, Passed: item.Passed, ReplyText: item.ReplyText, Interrupted: item.Interrupted, Error: item.Error, Finding: item.Finding})
	}
	return ret, nil
}
