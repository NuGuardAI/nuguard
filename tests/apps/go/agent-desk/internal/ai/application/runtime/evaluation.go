package runtime

import (
	"context"
	"encoding/csv"
	"strconv"
	"strings"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/toolx"
)

// OfflineEvaluationCase is an isolated customer-service evaluation sample.
// Expectations are intentionally declarative so the same baseline can evolve
// without changing the runner's request contract.
type OfflineEvaluationCase struct {
	ID       string         `json:"id"`
	Category string         `json:"category"`
	Message  string         `json:"message"`
	History  []string       `json:"history,omitempty"`
	Expect   map[string]any `json:"expect,omitempty"`
}

type OfflineEvaluationResult struct {
	CaseID      string `json:"caseId"`
	Category    string `json:"category"`
	Passed      bool   `json:"passed"`
	ReplyText   string `json:"replyText"`
	Interrupted bool   `json:"interrupted"`
	Error       string `json:"error,omitempty"`
	Finding     string `json:"finding,omitempty"`
}

type OfflineEvaluationReport struct {
	Total   int                       `json:"total"`
	Passed  int                       `json:"passed"`
	Results []OfflineEvaluationResult `json:"results"`
}

// OfflineEvaluationRunner executes only isolated Debug requests. The supplied
// runner makes it testable without a real model and lets callers choose an
// explicit Engine implementation for mode comparison.
type OfflineEvaluationRunner struct {
	run func(context.Context, RunInput) (*RunResult, error)
}

func NewOfflineEvaluationRunner(run func(context.Context, RunInput) (*RunResult, error)) *OfflineEvaluationRunner {
	return &OfflineEvaluationRunner{run: run}
}

func (r *OfflineEvaluationRunner) Run(ctx context.Context, agent models.AIAgent, config models.AIConfig, cases []OfflineEvaluationCase) OfflineEvaluationReport {
	report := OfflineEvaluationReport{Results: make([]OfflineEvaluationResult, 0, len(cases))}
	for _, item := range cases {
		result := OfflineEvaluationResult{CaseID: strings.TrimSpace(item.ID), Category: strings.TrimSpace(item.Category)}
		if r == nil || r.run == nil {
			result.Error, result.Finding = "evaluation runner is not configured", "runner_missing"
			report.Results = append(report.Results, result)
			continue
		}
		summary, err := r.run(ctx, RunInput{
			Conversation: models.Conversation{AIAgentID: agent.ID, LastMessageSummary: strings.Join(item.History, "\n")},
			UserMessage:  models.Message{SenderType: enums.IMSenderTypeCustomer, MessageType: enums.IMMessageTypeText, Content: strings.TrimSpace(item.Message), RequestID: "offline-eval:" + strings.TrimSpace(item.ID)},
			AIAgent:      agent,
			AIConfig:     config,
			Debug:        true,
		})
		if err != nil {
			result.Error, result.Finding = err.Error(), "engine_error"
			report.Results = append(report.Results, result)
			continue
		}
		if summary != nil {
			result.ReplyText = strings.TrimSpace(summary.ReplyText)
			result.Interrupted = summary.Interrupted
		}
		result.Passed, result.Finding = evaluateOfflineCase(item.Expect, summary)
		if result.Passed {
			report.Passed++
		}
		report.Results = append(report.Results, result)
	}
	report.Total = len(report.Results)
	return report
}

func (r OfflineEvaluationReport) CSV() (string, error) {
	var output strings.Builder
	writer := csv.NewWriter(&output)
	if err := writer.Write([]string{"caseId", "category", "passed", "interrupted", "finding", "error", "replyText"}); err != nil {
		return "", err
	}
	for _, item := range r.Results {
		if err := writer.Write([]string{item.CaseID, item.Category, strconv.FormatBool(item.Passed), strconv.FormatBool(item.Interrupted), item.Finding, item.Error, item.ReplyText}); err != nil {
			return "", err
		}
	}
	writer.Flush()
	return output.String(), writer.Error()
}

func evaluateOfflineCase(expect map[string]any, summary *RunResult) (bool, string) {
	if summary == nil || strings.TrimSpace(summary.ReplyText) == "" {
		return false, "empty_reply"
	}
	if requiresConfirmation, _ := expect["requiresConfirmation"].(bool); requiresConfirmation && !summary.Interrupted {
		return false, "confirmation_not_reached"
	}
	if maxWrites, ok := evaluationExpectationInt(expect["maxWriteToolCalls"]); ok {
		if maxWrites < 0 {
			return false, "invalid_expectation"
		}
		if writeToolCalls(summary) > maxWrites {
			return false, "write_tool_limit_exceeded"
		}
	}
	return true, ""
}

func evaluationExpectationInt(value any) (int, bool) {
	switch item := value.(type) {
	case int:
		return item, true
	case int64:
		return int(item), true
	case float64:
		return int(item), item == float64(int(item))
	default:
		return 0, false
	}
}

func writeToolCalls(summary *RunResult) int {
	if summary == nil {
		return 0
	}
	count := 0
	for _, code := range summary.InvokedToolCodes {
		switch toolx.NormalizeToolCodeAlias(code) {
		case toolx.GraphCreateTicketConfirm.Code, toolx.GraphHandoffConversation.Code:
			count++
		}
	}
	return count
}
