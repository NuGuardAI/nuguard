package runtrace

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/google/uuid"
	"github.com/guiyangyuan/eino-support-copilot/internal/domain"
	"github.com/guiyangyuan/eino-support-copilot/internal/platform/database"
	"github.com/jackc/pgx/v5"
)

var ErrNotFound = errors.New("agent run not found")

type StartParams struct {
	ConversationID    uuid.UUID
	MessageID         *uuid.UUID
	TicketID          *uuid.UUID
	Status            domain.RunStatus
	Mode              string
	ModelName         *string
	InputSummary      string
	ReplayedFromRunID *uuid.UUID
}

type Repository struct {
	db database.DBTX
}

func NewRepository(db database.DBTX) *Repository {
	return &Repository{db: db}
}

func (r *Repository) Start(ctx context.Context, params StartParams) (domain.AgentRun, error) {
	var run domain.AgentRun
	err := r.db.QueryRow(ctx, `
		insert into agent_runs (
			conversation_id, message_id, ticket_id, status, mode, model_name,
			input_summary, replayed_from_run_id, started_at
		)
		values ($1, $2, $3, $4, $5, $6, $7, $8,
			case when $4 = 'running' then now() else null end)
		returning id, conversation_id, message_id, ticket_id, status, mode, model_name,
		          input_summary, output_summary, usage, error_code, error_message,
		          replayed_from_run_id, started_at, completed_at, created_at
	`,
		params.ConversationID,
		params.MessageID,
		params.TicketID,
		params.Status,
		params.Mode,
		params.ModelName,
		params.InputSummary,
		params.ReplayedFromRunID,
	).Scan(
		&run.ID,
		&run.ConversationID,
		&run.MessageID,
		&run.TicketID,
		&run.Status,
		&run.Mode,
		&run.ModelName,
		&run.InputSummary,
		&run.OutputSummary,
		&run.Usage,
		&run.ErrorCode,
		&run.ErrorMessage,
		&run.ReplayedFromRunID,
		&run.StartedAt,
		&run.CompletedAt,
		&run.CreatedAt,
	)
	if err != nil {
		return domain.AgentRun{}, fmt.Errorf("start agent run: %w", err)
	}
	run.Steps = []domain.AgentRunStep{}
	return run, nil
}

func (r *Repository) AppendStep(ctx context.Context, event domain.AgentEvent) error {
	payload := event.Payload
	if len(payload) == 0 {
		payload = json.RawMessage(`{}`)
	}
	var details struct {
		Stage     *string         `json:"stage"`
		Tool      *string         `json:"tool"`
		Arguments json.RawMessage `json:"arguments"`
		Citation  json.RawMessage `json:"citation"`
		Code      *string         `json:"code"`
	}
	if err := json.Unmarshal(payload, &details); err != nil {
		return fmt.Errorf("decode agent event payload: %w", err)
	}
	if len(details.Arguments) == 0 {
		details.Arguments = json.RawMessage(`{}`)
	}
	citations := json.RawMessage(`[]`)
	if len(details.Citation) > 0 && string(details.Citation) != "null" {
		citations = append(append(json.RawMessage{}, '['), details.Citation...)
		citations = append(citations, ']')
	}
	command, err := r.db.Exec(ctx, `
		with run_lock as (
			select id from agent_runs where id = $2 for update
		), next_sequence as (
			select coalesce(max(s.sequence), -1) + 1 as sequence
			from agent_run_steps s join run_lock r on r.id = s.run_id
		)
		insert into agent_run_steps (
			id, run_id, sequence, event_type, stage, input_summary, tool_name,
			tool_arguments, result_summary, citations, error_code, created_at
		)
		select $1, $2, n.sequence, $3, $4, '{}'::jsonb, $5,
		       $6::jsonb, $7::jsonb, $8::jsonb, $9, $10
		from next_sequence n
		where exists (select 1 from run_lock)
	`, event.EventID, event.RunID, event.Type, details.Stage, details.Tool,
		details.Arguments, payload, citations, details.Code, event.Timestamp)
	if err != nil {
		return fmt.Errorf("append agent run step: %w", err)
	}
	if command.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

func (r *Repository) Complete(
	ctx context.Context,
	runID uuid.UUID,
	status domain.RunStatus,
	outputSummary string,
	usage json.RawMessage,
) error {
	if len(usage) == 0 {
		usage = json.RawMessage(`{}`)
	}
	command, err := r.db.Exec(ctx, `
		update agent_runs
		set status = $2,
		    output_summary = nullif($3, ''),
		    usage = $4,
		    completed_at = case when $2 in ('completed', 'failed', 'cancelled') then now() else null end
		where id = $1
	`, runID, status, outputSummary, usage)
	if err != nil {
		return fmt.Errorf("complete agent run: %w", err)
	}
	if command.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

func (r *Repository) Fail(ctx context.Context, runID uuid.UUID, code, message string) error {
	command, err := r.db.Exec(ctx, `
		update agent_runs
		set status = 'failed', error_code = $2, error_message = $3, completed_at = now()
		where id = $1
	`, runID, code, message)
	if err != nil {
		return fmt.Errorf("fail agent run: %w", err)
	}
	if command.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

func (r *Repository) Get(ctx context.Context, id uuid.UUID) (domain.AgentRun, error) {
	var run domain.AgentRun
	err := r.db.QueryRow(ctx, `
		select id, conversation_id, message_id, ticket_id, status, mode, model_name,
		       input_summary, output_summary, usage, error_code, error_message,
		       replayed_from_run_id, started_at, completed_at, created_at
		from agent_runs where id = $1
	`, id).Scan(
		&run.ID,
		&run.ConversationID,
		&run.MessageID,
		&run.TicketID,
		&run.Status,
		&run.Mode,
		&run.ModelName,
		&run.InputSummary,
		&run.OutputSummary,
		&run.Usage,
		&run.ErrorCode,
		&run.ErrorMessage,
		&run.ReplayedFromRunID,
		&run.StartedAt,
		&run.CompletedAt,
		&run.CreatedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.AgentRun{}, ErrNotFound
	}
	if err != nil {
		return domain.AgentRun{}, fmt.Errorf("get agent run: %w", err)
	}

	rows, err := r.db.Query(ctx, `
		select id, run_id, sequence, event_type, stage, input_summary,
		       tool_name, tool_arguments, result_summary, citations,
		       duration_ms, error_code, created_at
		from agent_run_steps where run_id = $1
		order by sequence
	`, id)
	if err != nil {
		return domain.AgentRun{}, fmt.Errorf("list agent run steps: %w", err)
	}
	defer rows.Close()
	run.Steps = []domain.AgentRunStep{}
	for rows.Next() {
		var step domain.AgentRunStep
		if err := rows.Scan(
			&step.ID,
			&step.RunID,
			&step.Sequence,
			&step.EventType,
			&step.Stage,
			&step.InputSummary,
			&step.ToolName,
			&step.ToolArguments,
			&step.ResultSummary,
			&step.Citations,
			&step.DurationMS,
			&step.ErrorCode,
			&step.CreatedAt,
		); err != nil {
			return domain.AgentRun{}, fmt.Errorf("scan agent run step: %w", err)
		}
		run.Steps = append(run.Steps, step)
	}
	if err := rows.Err(); err != nil {
		return domain.AgentRun{}, fmt.Errorf("iterate agent run steps: %w", err)
	}
	return run, nil
}
