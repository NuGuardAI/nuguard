package enums

import "testing"

func TestVectorDBTypeLabelIncludesLanceDB(t *testing.T) {
	if VectorDBTypeLanceDB != "lancedb" {
		t.Fatalf("VectorDBTypeLanceDB = %q, want %q", VectorDBTypeLanceDB, "lancedb")
	}
	if got := GetVectorDBTypeLabel(VectorDBTypeLanceDB); got != "LanceDB" {
		t.Fatalf("GetVectorDBTypeLabel(VectorDBTypeLanceDB) = %q, want %q", got, "LanceDB")
	}
}
