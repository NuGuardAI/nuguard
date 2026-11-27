package runtime

import (
	"context"
	"encoding/json"
	"strings"
	"time"

	workflowexecutor "agent-desk/internal/ai/runtime/workflow"
	"agent-desk/internal/models"
	"agent-desk/internal/repositories"

	"github.com/mlogclub/simple/sqls"
)

type Service struct {
	engine *AgentLoopEngine
}

const (
	workflowRunStatusCompleted   = 1
	workflowRunStatusInterrupted = 2
	workflowRunStatusFailed      = 3
)

func NewService() *Service {
	return NewServiceWithEngine(NewAgentLoopEngine())
}

func NewServiceWithEngine(engine *AgentLoopEngine) *Service {
	return &Service{engine: engine}
}

func (s *Service) Run(ctx context.Context, req RunInput) (*RunResult, error) {
	return s.engine.Run(ctx, req)
}

func (s *Service) Resume(ctx context.Context, req ResumeInput) (*RunResult, error) {
	return s.engine.Resume(ctx, req)
}

func (s *Service) RunOfflineEvaluation(ctx context.Context, agent models.AIAgent, config models.AIConfig, cases []OfflineEvaluationCase) (OfflineEvaluationReport, error) {
	runner := NewOfflineEvaluationRunner(s.engine.Run)
	return runner.Run(ctx, agent, config, cases), nil
}

func toWorkflowResult(result *workflowexecutor.Result, modelName string, workflow resolvedWorkflow, workflowRunID int64) *RunResult {
	if result == nil {
		return nil
	}
	trace := map[string]any{
		"status":            result.Status,
		"workflowId":        workflow.WorkflowID,
		"workflowVersionId": workflow.VersionID,
		"workflowRunId":     workflowRunID,
		"nodePath":          result.NodePath,
	}
	traceData, _ := json.Marshal(trace)
	return &RunResult{
		Status:            result.Status,
		ReplyText:         result.ReplyText,
		ModelName:         modelName,
		PromptTokens:      result.PromptTokens,
		CompletionTokens:  result.CompletionTokens,
		RetrieverCount:    result.RetrieverCount,
		WorkflowID:        workflow.WorkflowID,
		WorkflowVersionID: workflow.VersionID,
		WorkflowRunID:     workflowRunID,
		WorkflowNodePath:  append([]string(nil), result.NodePath...),
		TraceData:         string(traceData),
		CheckPointID:      result.CheckPointID,
		CheckPointData:    result.CheckPointData,
		Interrupted:       result.Interrupted,
		Interrupts:        toWorkflowInterruptSummaries(result.Interrupts),
	}
}

func toWorkflowInterruptSummaries(items []workflowexecutor.InterruptSummary) []InterruptContextSummary {
	if len(items) == 0 {
		return nil
	}
	ret := make([]InterruptContextSummary, 0, len(items))
	for _, item := range items {
		ret = append(ret, InterruptContextSummary{
			Type:        item.Type,
			ID:          item.ID,
			InfoPreview: item.InfoPreview,
		})
	}
	return ret
}

func writeWorkflowRun(req RunInput, workflow resolvedWorkflow, result *workflowexecutor.Result, errorMessage string) (int64, error) {
	return writeWorkflowRunWithExistingID(req, workflow, result, errorMessage, 0)
}

func writeWorkflowRunWithExistingID(req RunInput, workflow resolvedWorkflow, result *workflowexecutor.Result, errorMessage string, existingRunID int64) (int64, error) {
	if result == nil {
		return 0, nil
	}
	now := time.Now()
	endedAt := now
	nodeTypes := make(map[string]string, len(workflow.Definition.Nodes))
	for _, node := range workflow.Definition.Nodes {
		nodeTypes[node.ID] = node.Type
	}
	runStatus := workflowRunStatus(result.Status, errorMessage)
	var runID int64
	err := sqls.WithTransaction(func(ctx *sqls.TxContext) error {
		run := repositories.AIWorkflowRunRepository.Get(ctx.Tx, existingRunID)
		if run == nil {
			run = &models.AIWorkflowRun{
				WorkflowID:        workflow.WorkflowID,
				WorkflowVersionID: workflow.VersionID,
				ConversationID:    req.Conversation.ID,
				AIAgentID:         req.AIAgent.ID,
				MessageID:         req.UserMessage.ID,
				Status:            runStatus,
				StartedAt:         now,
				EndedAt:           &endedAt,
				InterruptType:     firstWorkflowInterruptType(result),
				InterruptNodeID:   firstWorkflowInterruptNodeID(result),
				ErrorMessage:      errorMessage,
			}
			if err := repositories.AIWorkflowRunRepository.Create(ctx.Tx, run); err != nil {
				return err
			}
		} else if err := repositories.AIWorkflowRunRepository.Updates(ctx.Tx, run.ID, map[string]any{
			"status":            runStatus,
			"ended_at":          &endedAt,
			"interrupt_type":    firstWorkflowInterruptType(result),
			"interrupt_node_id": firstWorkflowInterruptNodeID(result),
			"error_message":     errorMessage,
			"updated_at":        now,
		}); err != nil {
			return err
		}
		runID = run.ID
		nodeTraces := result.NodeTraces
		if len(nodeTraces) == 0 {
			nodeTraces = fallbackWorkflowNodeTraces(result.NodePath, nodeTypes, result.Status)
		}
		for _, nodeTrace := range nodeTraces {
			nodeRun := &models.AIWorkflowNodeRun{
				WorkflowRunID: run.ID,
				NodeID:        nodeTrace.NodeID,
				NodeType:      firstNonEmpty(nodeTrace.NodeType, nodeTypes[nodeTrace.NodeID]),
				Status:        workflowRunStatus(nodeTrace.Status, nodeTrace.ErrorMessage),
				InputPreview:  nodeTrace.InputPreview,
				OutputPreview: nodeTrace.OutputPreview,
				ErrorMessage:  nodeTrace.ErrorMessage,
				StartedAt:     now,
				EndedAt:       &endedAt,
				DurationMS:    nodeTrace.DurationMS,
			}
			if err := repositories.AIWorkflowNodeRunRepository.Create(ctx.Tx, nodeRun); err != nil {
				return err
			}
		}
		return nil
	})
	return runID, err
}

func workflowAgentRunStatus(status string, errorMessage string) string {
	if strings.TrimSpace(errorMessage) != "" || strings.TrimSpace(status) == "error" {
		return "failed"
	}
	if strings.TrimSpace(status) == "interrupted" {
		return "interrupted"
	}
	return "completed"
}

func workflowRunStatus(status string, errorMessage string) int {
	if strings.TrimSpace(errorMessage) != "" || strings.TrimSpace(status) == "error" {
		return workflowRunStatusFailed
	}
	switch strings.TrimSpace(status) {
	case "interrupted":
		return workflowRunStatusInterrupted
	default:
		return workflowRunStatusCompleted
	}
}

func fallbackWorkflowNodeTraces(nodePath []string, nodeTypes map[string]string, status string) []workflowexecutor.NodeTrace {
	ret := make([]workflowexecutor.NodeTrace, 0, len(nodePath))
	for _, nodeID := range nodePath {
		ret = append(ret, workflowexecutor.NodeTrace{
			NodeID:   nodeID,
			NodeType: nodeTypes[nodeID],
			Status:   status,
		})
	}
	return ret
}

func firstWorkflowInterruptType(result *workflowexecutor.Result) string {
	if result == nil || len(result.Interrupts) == 0 {
		return ""
	}
	return strings.TrimSpace(result.Interrupts[0].Type)
}

func firstWorkflowInterruptNodeID(result *workflowexecutor.Result) string {
	if result == nil || len(result.Interrupts) == 0 {
		return ""
	}
	return strings.TrimSpace(result.Interrupts[0].ID)
}

func firstNonEmpty(items ...string) string {
	for _, item := range items {
		if strings.TrimSpace(item) != "" {
			return strings.TrimSpace(item)
		}
	}
	return ""
}
