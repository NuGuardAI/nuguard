"""Shared literal redaction for AUTH evidence snippets (Go, TypeScript, ...).

AUTH detections copy a slice of the scanned source into ``ComponentDetection.snippet``,
which the extractor writes verbatim into ``Evidence.detail``. When that slice contains a
credential literal (``ClientSecret: "..."``, ``new OAuth2Client(id, "secret")``,
``sign(payload, { secret: "..." })``) the credential would be written into the SBOM.

:func:`redact_code_snippet` tokenizes the slice, replaces every string literal with
``"<redacted>"`` and drops comments. It never tries to decide whether a literal "looks
like" a secret. When the scanner may have lost sync (unterminated literal, a quote left
over after redaction, a snippet that starts mid-literal) it returns the caller-supplied
shape-only *fallback* so no literal text can leak.

:func:`redact_url_secrets` does the same for URLs stored in metadata or canonical names
(userinfo, query values and fragments are replaced).

Same approach as the C# adapter's ``_redacted_snippet``; any new AUTH adapter that copies
source text should call these helpers instead of writing its own.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit, urlunsplit

REDACTED = "<redacted>"
_REDACTED_LITERAL = f'"{REDACTED}"'
_RUNE_PLACEHOLDER = "'?'"

Language = Literal["go", "ts"]


def _skip_quoted(text: str, start: int, quote: str) -> int | None:
    """Index just past the single-line literal opened at *start*, or None if unterminated."""
    i = start + 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
        elif c == quote:
            return i + 1
        elif c == "\n":
            return None
        else:
            i += 1
    return None


def _skip_template(text: str, start: int) -> int | None:
    """Index just past the JS/TS template literal opened at *start* (handles ``${...}``)."""
    i = start + 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
        elif c == "`":
            return i + 1
        elif c == "$" and text.startswith("${", i):
            end = _skip_template_expr(text, i + 2)
            if end is None:
                return None
            i = end
        else:
            i += 1
    return None


def _skip_template_expr(text: str, start: int) -> int | None:
    """Index just past the ``}`` closing a ``${`` expression whose body starts at *start*."""
    i = start
    n = len(text)
    depth = 1
    while i < n:
        c = text[i]
        if c in "\"'":
            end = _skip_quoted(text, i, c)
            if end is None:
                return None
            i = end
        elif c == "`":
            end = _skip_template(text, i)
            if end is None:
                return None
            i = end
        elif c == "{":
            depth += 1
            i += 1
        elif c == "}":
            depth -= 1
            i += 1
            if depth == 0:
                return i
        else:
            i += 1
    return None


def _scan(text: str, language: Language) -> str | None:
    """Replace literals and comments in *text*; None when the scanner lost sync."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        end: int | None
        if ch == "/" and nxt == "/":
            newline = text.find("\n", i)
            i = n if newline == -1 else newline
            out.append(" ")
        elif ch == "/" and nxt == "*":
            close = text.find("*/", i + 2)
            if close == -1:
                return None
            i = close + 2
            out.append(" ")
        elif ch == '"':
            end = _skip_quoted(text, i, '"')
            if end is None:
                return None
            out.append(_REDACTED_LITERAL)
            i = end
        elif ch == "'":
            end = _skip_quoted(text, i, "'")
            if end is None:
                return None
            # Go: rune literal. TS/JS: a string.
            out.append(_RUNE_PLACEHOLDER if language == "go" else _REDACTED_LITERAL)
            i = end
        elif ch == "`":
            if language == "go":
                close = text.find("`", i + 1)
                end = None if close == -1 else close + 1
            else:
                end = _skip_template(text, i)
            if end is None:
                return None
            out.append(_REDACTED_LITERAL)
            i = end
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def redact_code_snippet(
    text: str,
    *,
    language: Language,
    fallback: str,
    limit: int = 240,
) -> str:
    """Return *text* with string literals and comments redacted, on one line.

    *fallback* is a shape-only snippet (e.g. ``"oauth2.Config{...}"``) returned when
    *text* is empty or cannot be redacted with confidence. *limit* truncates the
    result after redaction, never before, so a cut can't split a literal.
    """
    if not text or not text.strip():
        return fallback
    scanned = _scan(text, language)
    if scanned is None:
        return fallback
    residue = scanned.replace(_REDACTED_LITERAL, "").replace(_RUNE_PLACEHOLDER, "")
    if any(q in residue for q in "\"'`"):
        return fallback
    return " ".join(scanned.split())[:limit]


def redact_url_secrets(url: str) -> str:
    """Strip credentials from *url*: userinfo, query values and fragment.

    Scheme, host and path are kept so the URL still identifies the endpoint
    (``https://u:p@host/jwks?key=abc`` -> ``https://<redacted>@host/jwks?key=<redacted>``).
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return REDACTED
    netloc = parts.netloc
    if "@" in netloc:
        netloc = f"{REDACTED}@{netloc.rpartition('@')[2]}"
    query = parts.query
    if query:
        query = "&".join(
            f"{item.partition('=')[0]}={REDACTED}" if "=" in item else REDACTED
            for item in query.split("&")
        )
    fragment = REDACTED if parts.fragment else ""
    return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))


__all__ = ["REDACTED", "Language", "redact_code_snippet", "redact_url_secrets"]
