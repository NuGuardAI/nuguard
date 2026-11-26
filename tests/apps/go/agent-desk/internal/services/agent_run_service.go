package services

import (
	"regexp"
	"slices"
	"sort"
	"strings"
	"time"

	"agent-desk/internal/models"
	"agent-desk/internal/pkg/dto"
	"agent-desk/internal/pkg/dto/request"
	"agent-desk/internal/pkg/enums"
	"agent-desk/internal/pkg/errorsx"
	"agent-desk/internal/pkg/httpx/params"
	"agent-desk/internal/pkg/utils"
	"agent-desk/internal/repositories"

	"github.com/mlogclub/simple/sqls"
	"gorm.io/gorm"
)

var AgentRunService = newAgentRunService()

func newAgentRunService() *agentRunService {
	return &agentRunService{}
}

type agentRunService struct{}

type AgentRunMetrics struct {
	TotalRuns               int     `json:"totalRuns"`
	CompletedRuns           int     `json:"completedRuns"`
	FailedRuns              int     `json:"failedRuns"`
	InterruptedRuns         int     `json:"interruptedRuns"`
	CompletionRate          float64 `json:"completionRate"`
	ToolCalls               int     `json:"toolCalls"`
	ToolSuccessRate         float64 `json:"toolSuccessRate"`
	AverageSteps            float64 `json:"averageSteps"`
	AverageDurationMS       int64   `json:"averageDurationMs"`
	P95DurationMS           int64   `json:"p95DurationMs"`
	PromptTokens            int64   `json:"promptTokens"`
	CompletionTokens        int64   `json:"completionTokens"`
	HandoffRate             float64 `json:"handoffRate"`
	KnowledgeFallbackRate   float64 `json:"knowledgeFallbackRate"`
	ResumedInterrupts       int     `json:"resumedInterrupts"`
	ResolvedInterrupts      int     `json:"resolvedInterrupts"`
	InterruptRecoveryRate   float64 `json:"interruptRecoveryRate"`
	ReviewedRuns            int     `json:"reviewedRuns"`
	ResolvedRuns            int     `json:"resolvedRuns"`
	ResolutionRate          float64 `json:"resolutionRate"`
	UnsupportedEvidenceRuns int     `json:"unsupportedEvidenceRuns"`
	UnsupportedEvidenceRate float64 `json:"unsupportedEvidenceRate"`
}

const maxAgentAuditPreviewChars = 4000

var agentAuditSecretPattern = regexp.MustCompile(`(?i)(?:"|')?(api[_-]?key|authorization|password|secret|token|cookie)(?:"|')?\s*([:=])\s*(?:"[^"]*"|'[^']*'|[^\s,;}]+)`)

func (s *agentRunService) Get(id int64) *models.AgentRun {
	if id <= 0 {
		return nil
	}
	return repositories.AgentRunRepository.Get(sqls.DB(), id)
}

func (s *agentRunService) FindPageByParams(queryParams *params.QueryParams) (list []models.AgentRun, paging *sqls.Paging) {
	return repositories.AgentRunRepository.FindPageByParams(sqls.DB(), queryParams)
}

func (s *agentRunService) GetDetail(id int64) (*models.AgentRun, []models.AgentStep, []models.AgentToolCall) {
	run := s.Get(id)
	if run == nil {
		return nil, nil, nil
	}
	return run,
		repositories.AgentStepRepository.FindByAgentRunID(sqls.DB(), id),
		repositories.AgentToolCallRepository.FindByAgentRunID(sqls.DB(), id)
}

func (s *agentRunService) GetLatestStepID(agentRunID int64) int64 {
	step := repositories.AgentStepRepository.LastByAgentRunID(sqls.DB(), agentRunID)
	if step == nil {
		return 0
	}
	return step.ID
}

func (s *agentRunService) GetQualityFeedback(agentRunID int64) *models.AgentRunQualityFeedback {
	return repositories.AgentRunQualityFeedbackRepository.GetByAgentRunID(sqls.DB(), agentRunID)
}

func (s *agentRunService) SaveQualityFeedback(req request.SaveAgentRunQualityFeedbackRequest, operator *dto.AuthPrincipal) error {
	if operator == nil {
		return errorsx.UnauthorizedI18n("error.auth.expired")
	}
	if req.AgentRunID <= 0 {
		return errorsx.InvalidParam("agent run id is required")
	}
	if !slices.Contains(enums.AgentRunResolutionStatusValues, req.ResolutionStatus) || !slices.Contains(enums.AgentRunEvidenceStatusValues, req.EvidenceStatus) {
		return errorsx.InvalidParam("invalid agent run quality feedback status")
	}
	comment := strings.TrimSpace(req.Comment)
	if len([]rune(comment)) > 2000 {
		return errorsx.InvalidParam("agent run quality feedback comment is too long")
	}
	return sqls.WithTransaction(func(ctx *sqls.TxContext) error {
		if repositories.AgentRunRepository.Get(ctx.Tx, req.AgentRunID) == nil {
			return errorsx.InvalidParam("agent run does not exist")
		}
		current := repositories.AgentRunQualityFeedbackRepository.GetByAgentRunID(ctx.Tx, req.AgentRunID)
		if current == nil {
			return repositories.AgentRunQualityFeedbackRepository.Create(ctx.Tx, &models.AgentRunQualityFeedback{
				AgentRunID: req.AgentRunID, ResolutionStatus: req.ResolutionStatus, EvidenceStatus: req.EvidenceStatus, Comment: comment,
				AuditFields: utils.BuildAuditFields(operator),
			})
		}
		return repositories.AgentRunQualityFeedbackRepository.Updates(ctx.Tx, current.ID, map[string]any{
			"resolution_status": req.ResolutionStatus,
			"evidence_status":   req.EvidenceStatus,
			"comment":           comment,
			"update_user_id":    operator.UserID,
			"update_user_name":  operator.Username,
			"updated_at":        time.Now(),
		})
	})
}

// GetMetrics aggregates normalized audit records in Go so SQLite and MySQL
// use identical percentile and rate semantics.
func (s *agentRunService) GetMetrics(aiAgentID int64) AgentRunMetrics {
	runs := repositories.AgentRunRepository.FindRecent(sqls.DB(), aiAgentID, 5000)
	metrics := s.aggregateMetrics(sqls.DB(), runs)
	if len(runs) == 0 {
		return metrics
	}
	conversationCount := repositories.ConversationRepository.CountByAIAgentID(sqls.DB(), aiAgentID)
	if conversationCount > 0 {
		metrics.HandoffRate = float64(repositories.ConversationRepository.CountHandoffByAIAgentID(sqls.DB(), aiAgentID)) / float64(conversationCount)
	}
	return metrics
}

func (s *agentRunService) aggregateMetrics(db *gorm.DB, runs []models.AgentRun) AgentRunMetrics {
	metrics := AgentRunMetrics{TotalRuns: len(runs)}
	if len(runs) == 0 {
		return metrics
	}
	runIDs := make([]int64, 0, len(runs))
	durations := make([]int64, 0, len(runs))
	var durationTotal int64
	for _, run := range runs {
		runIDs = append(runIDs, run.ID)
		switch run.Status {
		case "completed":
			metrics.CompletedRuns++
		case "failed":
			metrics.FailedRuns++
		case "interrupted":
			metrics.InterruptedRuns++
		}
		metrics.PromptTokens += int64(run.PromptTokens)
		metrics.CompletionTokens += int64(run.CompletionTokens)
		if run.EndedAt != nil {
			duration := run.EndedAt.Sub(run.StartedAt).Milliseconds()
			if duration < 0 {
				duration = 0
			}
			durations = append(durations, duration)
			durationTotal += duration
		}
	}
	metrics.CompletionRate = float64(metrics.CompletedRuns) / float64(metrics.TotalRuns)
	if len(durations) > 0 {
		metrics.AverageDurationMS = durationTotal / int64(len(durations))
		sort.Slice(durations, func(i, j int) bool { return durations[i] < durations[j] })
		index := (len(durations)*95+99)/100 - 1
		metrics.P95DurationMS = durations[index]
	}
	steps := repositories.AgentStepRepository.FindByAgentRunIDs(db, runIDs)
	metrics.AverageSteps = float64(len(steps)) / float64(metrics.TotalRuns)
	fallbackRunIDs := make(map[int64]struct{})
	for _, step := range steps {
		if step.StepType == "policy" && step.StepCode == "knowledge_evidence" {
			fallbackRunIDs[step.AgentRunID] = struct{}{}
		}
	}
	metrics.KnowledgeFallbackRate = float64(len(fallbackRunIDs)) / float64(metrics.TotalRuns)
	toolCalls := repositories.AgentToolCallRepository.FindByAgentRunIDs(db, runIDs)
	metrics.ToolCalls = len(toolCalls)
	if len(toolCalls) > 0 {
		completed := 0
		for _, call := range toolCalls {
			if call.Status == "completed" {
				completed++
			}
		}
		metrics.ToolSuccessRate = float64(completed) / float64(len(toolCalls))
	}
	interrupts := repositories.ConversationInterruptRepository.FindByAgentRunIDs(db, runIDs)
	for _, interrupt := range interrupts {
		if interrupt.ResumeCount <= 0 {
			continue
		}
		metrics.ResumedInterrupts++
		if interrupt.Status == "resolved" {
			metrics.ResolvedInterrupts++
		}
	}
	if metrics.ResumedInterrupts > 0 {
		metrics.InterruptRecoveryRate = float64(metrics.ResolvedInterrupts) / float64(metrics.ResumedInterrupts)
	}
	feedbacks := repositories.AgentRunQualityFeedbackRepository.FindByAgentRunIDs(db, runIDs)
	metrics.ReviewedRuns = len(feedbacks)
	for _, feedback := range feedbacks {
		if feedback.ResolutionStatus == enums.AgentRunResolutionStatusResolved {
			metrics.ResolvedRuns++
		}
		if feedback.EvidenceStatus == enums.AgentRunEvidenceStatusUnsupported {
			metrics.UnsupportedEvidenceRuns++
		}
	}
	if metrics.ReviewedRuns > 0 {
		metrics.ResolutionRate = float64(metrics.ResolvedRuns) / float64(metrics.ReviewedRuns)
		metrics.UnsupportedEvidenceRate = float64(metrics.UnsupportedEvidenceRuns) / float64(metrics.ReviewedRuns)
	}
	return metrics
}

type AgentLoopRunInput struct {
	ConversationID    int64
	AIAgentID         int64
	AgentRevisionID   int64
	SourceMessageID   int64
	WorkflowRunID     int64
	Status            string
	PromptTokens      int
	CompletionTokens  int
	StartedAt         time.Time
	EndedAt           *time.Time
	ErrorMessage      string
	TraceData         string
	StepType          string
	StepCode          string
	StepInputPreview  string
	StepOutputPreview string
	AdditionalSteps   []AgentLoopStepInput
	ToolCalls         []AgentLoopToolCallInput
}

type AgentLoopStepInput struct {
	StepType      string
	StepCode      string
	WorkflowRunID int64
	Status        string
	InputPreview  string
	OutputPreview string
	ErrorMessage  string
}

type AgentLoopToolCallInput struct {
	ToolCode         string
	RiskLevel        string
	RequireConfirm   bool
	Status           string
	ArgumentsPreview string
	ResultPreview    string
	ErrorMessage     string
	DurationMS       int
}

// RecordResume closes or re-interrupts the original Agent Loop parent run,
// appends a normalized resume step, and records an optional resumed tool call.
func (s *agentRunService) RecordResume(db *gorm.DB, agentRunID, workflowRunID int64, status, replyText string, toolCall *AgentLoopToolCallInput) error {
	if agentRunID <= 0 {
		return nil
	}
	run := repositories.AgentRunRepository.Get(db, agentRunID)
	if run == nil {
		return nil
	}
	status = firstNonEmptyString(status, "completed")
	now := time.Now()
	if err := repositories.AgentRunRepository.Updates(db, run.ID, map[string]any{
		"status": status, "ended_at": &now, "error_message": "", "updated_at": now,
	}); err != nil {
		return err
	}
	step := &models.AgentStep{
		AgentRunID: run.ID, WorkflowRunID: workflowRunID,
		StepType: "resume", StepCode: "confirmation_resume", Status: status,
		InputPreview: "customer confirmation", OutputPreview: sanitizeAgentAuditPreview(replyText),
		StartedAt: now, EndedAt: &now, CreatedAt: now,
	}
	if err := repositories.AgentStepRepository.Create(db, step); err != nil {
		return err
	}
	if toolCall == nil {
		return nil
	}
	return repositories.AgentToolCallRepository.Create(db, &models.AgentToolCall{
		AgentRunID: run.ID, AgentStepID: step.ID, ToolCode: strings.TrimSpace(toolCall.ToolCode),
		RiskLevel: strings.TrimSpace(toolCall.RiskLevel), RequireConfirm: toolCall.RequireConfirm,
		Status: firstNonEmptyString(toolCall.Status, status), ArgumentsPreview: sanitizeAgentAuditPreview(toolCall.ArgumentsPreview),
		ResultPreview: sanitizeAgentAuditPreview(toolCall.ResultPreview), ErrorMessage: sanitizeAgentAuditPreview(toolCall.ErrorMessage),
		DurationMS: toolCall.DurationMS, CreatedAt: now,
	})
}

// RecordAgentLoopRun writes the Agent Loop parent audit run and its normalized
// root step in one transaction owned by the caller.
func (s *agentRunService) RecordAgentLoopRun(db *gorm.DB, input AgentLoopRunInput) (int64, error) {
	now := time.Now()
	startedAt := input.StartedAt
	if startedAt.IsZero() {
		startedAt = now
	}
	status := strings.TrimSpace(input.Status)
	if status == "" {
		status = "completed"
	}
	run := &models.AgentRun{
		ConversationID: input.ConversationID, AIAgentID: input.AIAgentID, AgentRevisionID: input.AgentRevisionID,
		SourceMessageID: input.SourceMessageID, WorkflowRunID: input.WorkflowRunID, Status: status,
		PromptTokens: input.PromptTokens, CompletionTokens: input.CompletionTokens, StartedAt: startedAt, EndedAt: input.EndedAt,
		ErrorMessage: sanitizeAgentAuditPreview(input.ErrorMessage), TraceData: sanitizeAgentAuditPreview(input.TraceData), CreatedAt: now, UpdatedAt: now,
	}
	if err := repositories.AgentRunRepository.Create(db, run); err != nil {
		return 0, err
	}
	durationMS := 0
	if input.EndedAt != nil {
		durationMS = int(input.EndedAt.Sub(startedAt).Milliseconds())
		if durationMS < 0 {
			durationMS = 0
		}
	}
	step := &models.AgentStep{
		AgentRunID: run.ID, StepType: strings.TrimSpace(input.StepType), StepCode: strings.TrimSpace(input.StepCode), Status: status,
		InputPreview: sanitizeAgentAuditPreview(input.StepInputPreview), OutputPreview: sanitizeAgentAuditPreview(input.StepOutputPreview), ErrorMessage: sanitizeAgentAuditPreview(input.ErrorMessage),
		StartedAt: startedAt, EndedAt: input.EndedAt, DurationMS: durationMS, CreatedAt: now,
	}
	if err := repositories.AgentStepRepository.Create(db, step); err != nil {
		return 0, err
	}
	for _, extra := range input.AdditionalSteps {
		extraStep := &models.AgentStep{
			AgentRunID: run.ID, WorkflowRunID: extra.WorkflowRunID, StepType: strings.TrimSpace(extra.StepType), StepCode: strings.TrimSpace(extra.StepCode),
			Status: firstNonEmptyString(extra.Status, status), InputPreview: sanitizeAgentAuditPreview(extra.InputPreview), OutputPreview: sanitizeAgentAuditPreview(extra.OutputPreview),
			ErrorMessage: sanitizeAgentAuditPreview(extra.ErrorMessage), StartedAt: startedAt, EndedAt: input.EndedAt, DurationMS: durationMS, CreatedAt: now,
		}
		if err := repositories.AgentStepRepository.Create(db, extraStep); err != nil {
			return 0, err
		}
	}
	for _, call := range input.ToolCalls {
		toolCall := &models.AgentToolCall{
			AgentRunID: run.ID, AgentStepID: step.ID, ToolCode: strings.TrimSpace(call.ToolCode), RiskLevel: strings.TrimSpace(call.RiskLevel),
			RequireConfirm: call.RequireConfirm, Status: firstNonEmptyString(call.Status, status), ArgumentsPreview: sanitizeAgentAuditPreview(call.ArgumentsPreview),
			ResultPreview: sanitizeAgentAuditPreview(call.ResultPreview), ErrorMessage: sanitizeAgentAuditPreview(call.ErrorMessage), DurationMS: call.DurationMS, CreatedAt: now,
		}
		if err := repositories.AgentToolCallRepository.Create(db, toolCall); err != nil {
			return 0, err
		}
	}
	return run.ID, nil
}

func sanitizeAgentAuditPreview(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ""
	}
	value = agentAuditSecretPattern.ReplaceAllString(value, "$1$2***")
	runes := []rune(value)
	if len(runes) <= maxAgentAuditPreviewChars {
		return value
	}
	return strings.TrimSpace(string(runes[:maxAgentAuditPreviewChars])) + "\n[preview truncated]"
}

func firstNonEmptyString(items ...string) string {
	for _, item := range items {
		if value := strings.TrimSpace(item); value != "" {
			return value
		}
	}
	return ""
}
