//go:build !lancedb

package vectordb

import (
	"fmt"

	"agent-desk/internal/pkg/config"
)

func NewLanceDBProvider(_ *config.LanceDBVectorDBConfig) (Provider, error) {
	return nil, fmt.Errorf("LanceDB provider is not built. Rebuild with -tags lancedb and configure LanceDB native libraries")
}
