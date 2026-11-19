package evaluation

import "time"

type ExpectedTool struct {
	Name      string            `json:"name"`
	Arguments map[string]string `json:"arguments"`
}

type ExpectedCitation struct {
	Title   string `json:"title"`
	Locator string `json:"locator"`
}

type Case struct {
	ID                     string             `json:"id"`
	Input                  string             `json:"input"`
	ExpectedIntent         string             `json:"expected_intent"`
	ExpectedPriority       string             `json:"expected_priority"`
	ExpectedTools          []ExpectedTool     `json:"expected_tools"`
	RequiredCitations      []ExpectedCitation `json:"required_citations"`
	ExpectedRequiresReview bool               `json:"requires_review"`
}

type Dataset struct {
	Name    string `json:"name"`
	Version string `json:"version"`
	Cases   []Case `json:"cases"`
}

type ToolCall struct {
	Name      string         `json:"name"`
	Arguments map[string]any `json:"arguments"`
}

type Citation struct {
	Title   string `json:"title"`
	Locator string `json:"locator"`
}

type Actual struct {
	Intent         string     `json:"intent"`
	Priority       string     `json:"priority"`
	Tools          []ToolCall `json:"tools"`
	Citations      []Citation `json:"citations"`
	Answer         string     `json:"answer"`
	RequiresReview bool       `json:"requires_review"`
	SchemaPass     bool       `json:"schema_pass"`
	TTFTMS         int64      `json:"ttft_ms"`
	TotalLatencyMS int64      `json:"total_latency_ms"`
}

type Metric struct {
	Numerator   float64  `json:"numerator"`
	Denominator int64    `json:"denominator"`
	Value       *float64 `json:"value,omitempty"`
	Unit        string   `json:"unit,omitempty"`
}

type CaseResult struct {
	CaseID    string             `json:"case_id"`
	Passed    bool               `json:"passed"`
	Scores    map[string]float64 `json:"scores"`
	Actual    Actual             `json:"actual"`
	LatencyMS int64              `json:"latency_ms"`
	ErrorCode *string            `json:"error_code,omitempty"`
}

type RunConfig struct {
	CommitSHA string    `json:"commit_sha"`
	Mode      string    `json:"mode"`
	Timestamp time.Time `json:"timestamp"`
}

type RunResult struct {
	ID          string            `json:"id,omitempty"`
	Status      string            `json:"status"`
	DatasetName string            `json:"dataset_name"`
	Version     string            `json:"version"`
	DatasetHash string            `json:"dataset_hash"`
	Config      RunConfig         `json:"config"`
	Metrics     map[string]Metric `json:"metrics"`
	Results     []CaseResult      `json:"results"`
	Completed   int               `json:"completed"`
	Total       int               `json:"total"`
}
