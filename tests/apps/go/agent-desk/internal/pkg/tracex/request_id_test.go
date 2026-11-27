package tracex

import "testing"

func TestNormalizeRequestID(t *testing.T) {
	if got := NormalizeRequestID("  trace-123  "); got != "trace-123" {
		t.Fatalf("NormalizeRequestID()=%q want %q", got, "trace-123")
	}
	if got := NormalizeRequestID("bad\nid"); got != "" {
		t.Fatalf("NormalizeRequestID()=%q want empty", got)
	}
	if got := NormalizeRequestID(""); got != "" {
		t.Fatalf("NormalizeRequestID()=%q want empty", got)
	}
}

func TestEnsureRequestID(t *testing.T) {
	if got := EnsureRequestID("trace-123"); got != "trace-123" {
		t.Fatalf("EnsureRequestID(existing)=%q want %q", got, "trace-123")
	}
	if got := EnsureRequestID(""); got == "" {
		t.Fatalf("EnsureRequestID(empty) should generate a value")
	}
}
