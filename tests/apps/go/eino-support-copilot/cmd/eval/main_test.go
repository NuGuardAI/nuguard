package main

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/guiyangyuan/eino-support-copilot/internal/evaluation"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestWriteResultCreatesFormattedJSONFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "mock-baseline.json")
	result := evaluation.RunResult{
		Status:    "completed",
		Config:    evaluation.RunConfig{CommitSHA: "abc123", Mode: "mock"},
		Completed: 24,
		Total:     24,
	}

	require.NoError(t, writeResult(path, result))
	data, err := os.ReadFile(path)
	require.NoError(t, err)
	assert.Contains(t, string(data), "\n  \"status\": \"completed\"")
	assert.Contains(t, string(data), "\"commit_sha\": \"abc123\"")
}
