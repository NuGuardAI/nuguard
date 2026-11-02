package evaluation

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var ErrRunNotFound = errors.New("evaluation run not found")

type Manager struct {
	pool    *pgxpool.Pool
	runner  *Runner
	dataset Dataset
}

func NewManager(pool *pgxpool.Pool, runner *Runner, dataset Dataset) *Manager {
	return &Manager{pool: pool, runner: runner, dataset: dataset}
}

func (m *Manager) Start(ctx context.Context) (RunResult, error) {
	if m.pool == nil || m.runner == nil {
		return RunResult{}, errors.New("evaluation manager is unavailable")
	}
	hash, err := HashDataset(m.dataset)
	if err != nil {
		return RunResult{}, err
	}
	tx, err := m.pool.Begin(ctx)
	if err != nil {
		return RunResult{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	var datasetID uuid.UUID
	err = tx.QueryRow(ctx, `
		insert into eval_datasets (name, version, dataset_hash)
		values ($1, $2, $3)
		on conflict (name, version) do update set dataset_hash = excluded.dataset_hash
		returning id
	`, m.dataset.Name, m.dataset.Version, hash).Scan(&datasetID)
	if err != nil {
		return RunResult{}, fmt.Errorf("upsert evaluation dataset: %w", err)
	}
	for _, testCase := range m.dataset.Cases {
		tools, _ := json.Marshal(testCase.ExpectedTools)
		citations, _ := json.Marshal(testCase.RequiredCitations)
		if _, err := tx.Exec(ctx, `
			insert into eval_cases (
				dataset_id, case_key, input, expected_intent, expected_priority,
				expected_tools, expected_citations, expected_requires_review
			) values ($1,$2,$3,$4,$5,$6,$7,$8)
			on conflict (dataset_id, case_key) do update set
				input=excluded.input, expected_intent=excluded.expected_intent,
				expected_priority=excluded.expected_priority, expected_tools=excluded.expected_tools,
				expected_citations=excluded.expected_citations,
				expected_requires_review=excluded.expected_requires_review
		`, datasetID, testCase.ID, testCase.Input, testCase.ExpectedIntent, testCase.ExpectedPriority, tools, citations, testCase.ExpectedRequiresReview); err != nil {
			return RunResult{}, fmt.Errorf("upsert evaluation case: %w", err)
		}
	}
	config := m.runner.config
	config.Timestamp = time.Now().UTC()
	configJSON, _ := json.Marshal(config)
	var runID uuid.UUID
	if err := tx.QueryRow(ctx, `
		insert into eval_runs (dataset_id, status, mode, commit_sha, config)
		values ($1, 'queued', $2, $3, $4) returning id
	`, datasetID, config.Mode, config.CommitSHA, configJSON).Scan(&runID); err != nil {
		return RunResult{}, fmt.Errorf("create evaluation run: %w", err)
	}
	if err := tx.Commit(ctx); err != nil {
		return RunResult{}, err
	}
	go m.execute(context.WithoutCancel(ctx), runID)
	return RunResult{ID: runID.String(), Status: "queued", DatasetName: m.dataset.Name, Version: m.dataset.Version, DatasetHash: hash, Config: config, Metrics: map[string]Metric{}, Results: []CaseResult{}, Total: len(m.dataset.Cases)}, nil
}

func (m *Manager) execute(parent context.Context, runID uuid.UUID) {
	ctx, cancel := context.WithTimeout(parent, 10*time.Minute)
	defer cancel()
	_, _ = m.pool.Exec(ctx, `update eval_runs set status='running', started_at=now() where id=$1`, runID)
	result, err := m.runner.Run(ctx, m.dataset)
	if err != nil {
		_, _ = m.pool.Exec(context.WithoutCancel(ctx), `update eval_runs set status='failed', completed_at=now() where id=$1`, runID)
		return
	}
	tx, err := m.pool.Begin(context.WithoutCancel(ctx))
	if err != nil {
		return
	}
	defer func() { _ = tx.Rollback(context.WithoutCancel(ctx)) }()
	for _, caseResult := range result.Results {
		var caseID uuid.UUID
		if err := tx.QueryRow(context.WithoutCancel(ctx), `select id from eval_cases where dataset_id=(select dataset_id from eval_runs where id=$1) and case_key=$2`, runID, caseResult.CaseID).Scan(&caseID); err != nil {
			return
		}
		scores, _ := json.Marshal(caseResult.Scores)
		actual, _ := json.Marshal(caseResult.Actual)
		if _, err := tx.Exec(context.WithoutCancel(ctx), `
			insert into eval_results (eval_run_id, eval_case_id, passed, scores, actual, latency_ms, error_code)
			values ($1,$2,$3,$4,$5,$6,$7)
			on conflict (eval_run_id, eval_case_id) do update set passed=excluded.passed, scores=excluded.scores, actual=excluded.actual, latency_ms=excluded.latency_ms, error_code=excluded.error_code
		`, runID, caseID, caseResult.Passed, scores, actual, caseResult.LatencyMS, caseResult.ErrorCode); err != nil {
			return
		}
	}
	metrics, _ := json.Marshal(result.Metrics)
	config, _ := json.Marshal(result.Config)
	if _, err := tx.Exec(context.WithoutCancel(ctx), `update eval_runs set status='completed', metrics=$2, config=$3, completed_at=now() where id=$1`, runID, metrics, config); err != nil {
		return
	}
	_ = tx.Commit(context.WithoutCancel(ctx))
}

func (m *Manager) Get(ctx context.Context, id uuid.UUID) (RunResult, error) {
	var result RunResult
	var configJSON, metricsJSON []byte
	err := m.pool.QueryRow(ctx, `
		select r.id::text, r.status, d.name, d.version, d.dataset_hash, r.config, r.metrics,
		       (select count(*) from eval_cases c where c.dataset_id=d.id)
		from eval_runs r join eval_datasets d on d.id=r.dataset_id where r.id=$1
	`, id).Scan(&result.ID, &result.Status, &result.DatasetName, &result.Version, &result.DatasetHash, &configJSON, &metricsJSON, &result.Total)
	if errors.Is(err, pgx.ErrNoRows) {
		return RunResult{}, ErrRunNotFound
	}
	if err != nil {
		return RunResult{}, fmt.Errorf("get evaluation run: %w", err)
	}
	if err := json.Unmarshal(configJSON, &result.Config); err != nil {
		return RunResult{}, err
	}
	if err := json.Unmarshal(metricsJSON, &result.Metrics); err != nil {
		return RunResult{}, err
	}
	result.Results = []CaseResult{}
	rows, err := m.pool.Query(ctx, `
		select c.case_key, er.passed, er.scores, er.actual, er.latency_ms, er.error_code
		from eval_results er join eval_cases c on c.id=er.eval_case_id
		where er.eval_run_id=$1 order by c.case_key
	`, id)
	if err != nil {
		return RunResult{}, err
	}
	defer rows.Close()
	for rows.Next() {
		var item CaseResult
		var scoresJSON, actualJSON []byte
		if err := rows.Scan(&item.CaseID, &item.Passed, &scoresJSON, &actualJSON, &item.LatencyMS, &item.ErrorCode); err != nil {
			return RunResult{}, err
		}
		if err := json.Unmarshal(scoresJSON, &item.Scores); err != nil {
			return RunResult{}, err
		}
		if err := json.Unmarshal(actualJSON, &item.Actual); err != nil {
			return RunResult{}, err
		}
		result.Results = append(result.Results, item)
	}
	if err := rows.Err(); err != nil {
		return RunResult{}, err
	}
	result.Completed = len(result.Results)
	return result, nil
}
