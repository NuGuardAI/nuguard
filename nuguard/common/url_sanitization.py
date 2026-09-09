"""Utilities for removing credentials from repository URLs and diagnostics."""

from __future__ import annotations

from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "github_token",
        "key",
        "password",
        "private_token",
        "secret",
        "token",
    }
)
_REDACTED = "REDACTED"


def sanitize_repository_url(url: str) -> str:
    """Return *url* without userinfo or sensitive query-parameter values."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return _strip_userinfo_fallback(url)

    netloc = parsed.netloc.rsplit("@", 1)[-1]
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    sanitized_query = urlencode(
        [
            (key, _REDACTED if key.casefold() in _SENSITIVE_QUERY_KEYS else value)
            for key, value in query_items
        ],
        doseq=True,
    )
    return urlunsplit((parsed.scheme, netloc, parsed.path, sanitized_query, parsed.fragment))


def redact_repository_url_from_text(text: str, url: str) -> str:
    """Remove credentials from diagnostics that may contain *url* or its parts."""
    sanitized_url = sanitize_repository_url(url)
    redacted = text.replace(url, sanitized_url)

    try:
        parsed = urlsplit(url)
        secrets = [parsed.username, parsed.password]
        secrets.extend(
            value
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() in _SENSITIVE_QUERY_KEYS
        )
    except ValueError:
        secrets = []

    for secret in filter(None, secrets):
        for candidate in {secret, quote(secret, safe=""), unquote(secret)}:
            if candidate:
                redacted = redacted.replace(candidate, _REDACTED)
    return redacted


def _strip_userinfo_fallback(url: str) -> str:
    scheme_separator = url.find("://")
    if scheme_separator < 0:
        return url
    authority_start = scheme_separator + 3
    authority_end = len(url)
    for separator in ("/", "?", "#"):
        index = url.find(separator, authority_start)
        if index >= 0:
            authority_end = min(authority_end, index)
    authority = url[authority_start:authority_end]
    if "@" not in authority:
        return url
    return url[:authority_start] + authority.rsplit("@", 1)[-1] + url[authority_end:]