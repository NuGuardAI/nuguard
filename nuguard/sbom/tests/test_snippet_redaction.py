"""Unit tests for the shared AUTH snippet redaction helpers (issue #733)."""

from __future__ import annotations

import pytest

from nuguard.sbom.adapters._snippet_redaction import (
    REDACTED,
    Language,
    redact_code_snippet,
    redact_url_secrets,
)

_FALLBACK = "shape-only"


@pytest.mark.parametrize(
    ("language", "text", "expected"),
    [
        # Go: interpreted, raw and rune literals; comments dropped
        ("go", 'ClientSecret: "abc123",', 'ClientSecret: "<redacted>",'),
        ("go", "key := `raw\nsecret`", 'key := "<redacted>"'),
        ("go", "x := 'a'", "x := '?'"),
        ("go", 'a := "x" // password hunter2', 'a := "<redacted>"'),
        ("go", 'a := "x" /* hunter2 */ + b', 'a := "<redacted>" + b'),
        ("go", 'a := "say \\"hi\\" S3CRET"', 'a := "<redacted>"'),
        (
            "go",
            'oauth2.Config{\n\tClientID:     "id",\n\tClientSecret: "s3",\n'
            '\tScopes: []string{"email"},\n}',
            'oauth2.Config{ ClientID: "<redacted>", ClientSecret: "<redacted>", '
            'Scopes: []string{"<redacted>"}, }',
        ),
        # TypeScript: single/double quotes, template literals (nested), comments
        ("ts", "const s = 'it\\'s';", 'const s = "<redacted>";'),
        ("ts", 'f(`a${g("b")}c`, d)', 'f("<redacted>", d)'),
        ("ts", "f(`a${ {x: 1}.x }b`)", 'f("<redacted>")'),
        (
            "ts",
            "new OAuth2Client(\"id\", 'sec', `t`) // tail",
            'new OAuth2Client("<redacted>", "<redacted>", "<redacted>")',
        ),
        (
            "ts",
            'this.jwtService.sign({ sub: id }, { secret: "S3" });',
            'this.jwtService.sign({ sub: id }, { secret: "<redacted>" });',
        ),
        ("ts", 'a = "http://x.com"; // c', 'a = "<redacted>";'),
        # Code without literals is kept as written (whitespace collapsed)
        ("go", "jwt.Parse(tokenString,   keyFunc)", "jwt.Parse(tokenString, keyFunc)"),
        ("ts", "  client.verifyIdToken({ idToken });", "client.verifyIdToken({ idToken });"),
    ],
)
def test_literals_and_comments_are_redacted(language: Language, text: str, expected: str) -> None:
    assert redact_code_snippet(text, language=language, fallback=_FALLBACK) == expected


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("go", 'x := "S3CRET'),  # unterminated
        ("go", 'x := "S3CRET\nmore"'),  # interpreted strings cannot span lines
        ("go", "x := `S3CRET"),
        ("go", "/* S3CRET"),
        ("go", ""),
        ("go", "   "),
        ("ts", "f(`S3CRET"),
        ("ts", 'f(`a${g("S3CRET"'),  # unterminated template expression
        ("ts", 'S3CRET", more'),  # snippet starts inside a literal
    ],
)
def test_scanner_desync_returns_shape_only_fallback(language: Language, text: str) -> None:
    result = redact_code_snippet(text, language=language, fallback=_FALLBACK)
    assert result == _FALLBACK
    assert "S3CRET" not in result


def test_limit_applies_after_redaction() -> None:
    text = 'x("' + "A" * 500 + '")'
    result = redact_code_snippet(text, language="ts", fallback=_FALLBACK, limit=160)
    assert result == 'x("<redacted>")'


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://user:pw@host/x?api_key=SECRETKEY&b=1#frag",
            f"https://{REDACTED}@host/x?api_key={REDACTED}&b={REDACTED}#{REDACTED}",
        ),
        ("https://h/jwks?tok123", f"https://h/jwks?{REDACTED}"),
        ("https://appleid.apple.com/auth/keys", "https://appleid.apple.com/auth/keys"),
        ("", ""),
        ("https://[bad/x?k=v", REDACTED),
    ],
)
def test_redact_url_secrets(url: str, expected: str) -> None:
    assert redact_url_secrets(url) == expected
