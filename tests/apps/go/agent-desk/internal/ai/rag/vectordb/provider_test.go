//go:build !lancedb

package vectordb

import (
	"strings"
	"testing"

	"agent-desk/internal/pkg/config"
)

func TestInitLanceDBWithoutBuildTagReturnsActionableError(t *testing.T) {
	err := Init(&config.VectorDBConfig{
		Type: "lancedb",
		LanceDB: config.LanceDBVectorDBConfig{
			Path: "data/lancedb",
		},
	})
	if err == nil {
		t.Fatal("Init(lancedb) error = nil, want actionable build tag error")
	}
	if !strings.Contains(err.Error(), "LanceDB provider is not built") {
		t.Fatalf("Init(lancedb) error = %q, want build tag guidance", err.Error())
	}
}
