package config

import (
	"testing"

	"gopkg.in/yaml.v3"
)

func TestVectorDBConfigUnmarshalNestedProviders(t *testing.T) {
	raw := []byte(`
vectorDB:
  type: lancedb
  qdrant:
    host: 127.0.0.1
    grpcPort: 6334
    apiKey: secret
    useTls: true
  lancedb:
    path: data/lancedb
`)

	var cfg Config
	if err := yaml.Unmarshal(raw, &cfg); err != nil {
		t.Fatalf("yaml.Unmarshal() error = %v", err)
	}

	if cfg.VectorDB.Type != "lancedb" {
		t.Fatalf("VectorDB.Type = %q, want %q", cfg.VectorDB.Type, "lancedb")
	}
	if cfg.VectorDB.Qdrant.Host != "127.0.0.1" {
		t.Fatalf("VectorDB.Qdrant.Host = %q, want %q", cfg.VectorDB.Qdrant.Host, "127.0.0.1")
	}
	if cfg.VectorDB.Qdrant.GrpcPort != 6334 {
		t.Fatalf("VectorDB.Qdrant.GrpcPort = %d, want %d", cfg.VectorDB.Qdrant.GrpcPort, 6334)
	}
	if cfg.VectorDB.Qdrant.APIKey != "secret" {
		t.Fatalf("VectorDB.Qdrant.APIKey = %q, want %q", cfg.VectorDB.Qdrant.APIKey, "secret")
	}
	if !cfg.VectorDB.Qdrant.UseTLS {
		t.Fatal("VectorDB.Qdrant.UseTLS = false, want true")
	}
	if cfg.VectorDB.LanceDB.Path != "data/lancedb" {
		t.Fatalf("VectorDB.LanceDB.Path = %q, want %q", cfg.VectorDB.LanceDB.Path, "data/lancedb")
	}
}
