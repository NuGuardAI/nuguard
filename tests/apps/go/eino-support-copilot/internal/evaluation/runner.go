package evaluation

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"
)

var ErrInvalidDataset = errors.New("invalid evaluation dataset")

type Executor interface {
	Execute(context.Context, Case) (Actual, error)
}

type Runner struct {
	executor Executor
	config   RunConfig
}

func NewRunner(executor Executor, config RunConfig) *Runner {
	return &Runner{executor: executor, config: config}
}

func (r *Runner) Run(ctx context.Context, dataset Dataset) (RunResult, error) {
	if r.executor == nil || strings.TrimSpace(dataset.Name) == "" || strings.TrimSpace(dataset.Version) == "" || len(dataset.Cases) == 0 {
		return RunResult{}, ErrInvalidDataset
	}
	hash, err := HashDataset(dataset)
	if err != nil {
		return RunResult{}, err
	}
	config := r.config
	config.Timestamp = time.Now().UTC()
	result := RunResult{
		Status: "completed", DatasetName: dataset.Name, Version: dataset.Version,
		DatasetHash: hash, Config: config, Results: make([]CaseResult, 0, len(dataset.Cases)), Total: len(dataset.Cases),
	}
	for _, testCase := range dataset.Cases {
		actual, executeErr := r.executor.Execute(ctx, testCase)
		caseResult := scoreCase(testCase, actual, executeErr)
		result.Results = append(result.Results, caseResult)
		result.Completed++
	}
	result.Metrics = aggregateMetrics(dataset.Cases, result.Results)
	return result, nil
}

func scoreCase(testCase Case, actual Actual, executeErr error) CaseResult {
	scores := map[string]float64{
		"intent":      boolScore(actual.Intent == testCase.ExpectedIntent),
		"priority":    boolScore(actual.Priority == testCase.ExpectedPriority),
		"tools":       ScoreToolCalls(testCase.ExpectedTools, actual.Tools),
		"citations":   ScoreCitations(testCase.RequiredCitations, actual.Citations),
		"review_gate": boolScore(actual.RequiresReview == testCase.ExpectedRequiresReview),
		"schema":      boolScore(actual.SchemaPass),
	}
	passed := executeErr == nil
	for _, score := range scores {
		passed = passed && score == 1
	}
	var errorCode *string
	if executeErr != nil {
		code := "EXECUTION_FAILED"
		errorCode = &code
	}
	return CaseResult{CaseID: testCase.ID, Passed: passed, Scores: scores, Actual: actual, LatencyMS: actual.TotalLatencyMS, ErrorCode: errorCode}
}

func aggregateMetrics(cases []Case, results []CaseResult) map[string]Metric {
	metrics := map[string]Metric{}
	for index, result := range results {
		addMetric(metrics, "intent_accuracy", result.Scores["intent"], 1, "ratio")
		addMetric(metrics, "tool_accuracy", result.Scores["tools"], 1, "ratio")
		addMetric(metrics, "citation_precision", result.Scores["citations"], 1, "ratio")
		addMetric(metrics, "schema_pass_rate", result.Scores["schema"], 1, "ratio")
		if len(cases[index].RequiredCitations) > 0 {
			grounded := boolScore(result.Scores["citations"] == 1 && strings.TrimSpace(result.Actual.Answer) != "")
			addMetric(metrics, "evidence_grounded_answer_rate", grounded, 1, "ratio")
		} else {
			addMetric(metrics, "unsupported_refusal_rate", boolScore(result.Actual.RequiresReview), 1, "ratio")
		}
		addMetric(metrics, "ttft_ms", float64(result.Actual.TTFTMS), 1, "ms")
		addMetric(metrics, "total_latency_ms", float64(result.Actual.TotalLatencyMS), 1, "ms")
	}
	return metrics
}

func addMetric(metrics map[string]Metric, name string, numerator float64, denominator int64, unit string) {
	metric := metrics[name]
	metric.Numerator += numerator
	metric.Denominator += denominator
	metric.Unit = unit
	if metric.Denominator > 0 {
		value := metric.Numerator / float64(metric.Denominator)
		metric.Value = &value
	}
	metrics[name] = metric
}

func boolScore(value bool) float64 {
	if value {
		return 1
	}
	return 0
}

func HashDataset(dataset Dataset) (string, error) {
	encoded, err := json.Marshal(dataset)
	if err != nil {
		return "", fmt.Errorf("encode dataset hash: %w", err)
	}
	hash := sha256.Sum256(encoded)
	return hex.EncodeToString(hash[:]), nil
}

func LoadJSONL(path string) (Dataset, error) {
	file, err := os.Open(path)
	if err != nil {
		return Dataset{}, fmt.Errorf("open dataset: %w", err)
	}
	defer file.Close()
	dataset := Dataset{Name: "support-golden", Version: "1.0.0", Cases: []Case{}}
	seen := map[string]struct{}{}
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		if strings.TrimSpace(scanner.Text()) == "" {
			continue
		}
		var testCase Case
		if err := json.Unmarshal(scanner.Bytes(), &testCase); err != nil {
			return Dataset{}, fmt.Errorf("%w: decode case: %v", ErrInvalidDataset, err)
		}
		if testCase.ID == "" || testCase.Input == "" || testCase.ExpectedIntent == "" || testCase.ExpectedPriority == "" {
			return Dataset{}, ErrInvalidDataset
		}
		if _, exists := seen[testCase.ID]; exists {
			return Dataset{}, fmt.Errorf("%w: duplicate case %s", ErrInvalidDataset, testCase.ID)
		}
		seen[testCase.ID] = struct{}{}
		dataset.Cases = append(dataset.Cases, testCase)
	}
	if err := scanner.Err(); err != nil {
		return Dataset{}, fmt.Errorf("read dataset: %w", err)
	}
	if len(dataset.Cases) == 0 {
		return Dataset{}, ErrInvalidDataset
	}
	return dataset, nil
}

type ExpectedExecutor struct{}

func (ExpectedExecutor) Execute(_ context.Context, testCase Case) (Actual, error) {
	tools := make([]ToolCall, 0, len(testCase.ExpectedTools))
	for _, expected := range testCase.ExpectedTools {
		arguments := make(map[string]any, len(expected.Arguments))
		for key, value := range expected.Arguments {
			arguments[key] = value
		}
		tools = append(tools, ToolCall{Name: expected.Name, Arguments: arguments})
	}
	citations := make([]Citation, 0, len(testCase.RequiredCitations))
	for _, expected := range testCase.RequiredCitations {
		citations = append(citations, Citation{Title: expected.Title, Locator: expected.Locator})
	}
	answer := "Generated fixture answer grounded in the required evidence."
	if len(citations) == 0 {
		answer = "The generated evidence is insufficient; a human review is required."
	}
	return Actual{Intent: testCase.ExpectedIntent, Priority: testCase.ExpectedPriority, Tools: tools, Citations: citations, Answer: answer, RequiresReview: testCase.ExpectedRequiresReview, SchemaPass: true, TTFTMS: 8, TotalLatencyMS: 12}, nil
}
