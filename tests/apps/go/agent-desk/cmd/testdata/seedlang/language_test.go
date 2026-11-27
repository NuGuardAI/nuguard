package seedlang

import "testing"

func TestParseDefaultsToChinese(t *testing.T) {
	lang, err := Parse("")
	if err != nil {
		t.Fatalf("Parse empty returned error: %v", err)
	}
	if lang != Chinese {
		t.Fatalf("Parse empty = %q, want %q", lang, Chinese)
	}
}

func TestParseEnglishAliases(t *testing.T) {
	for _, raw := range []string{"en", "EN", "english", "English"} {
		lang, err := Parse(raw)
		if err != nil {
			t.Fatalf("Parse(%q) returned error: %v", raw, err)
		}
		if lang != English {
			t.Fatalf("Parse(%q) = %q, want %q", raw, lang, English)
		}
	}
}

func TestParseRejectsUnsupportedLanguage(t *testing.T) {
	if _, err := Parse("fr"); err == nil {
		t.Fatal("Parse unsupported language returned nil error")
	}
}
