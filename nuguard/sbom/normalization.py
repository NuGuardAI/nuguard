from __future__ import annotations

import re

_TEMPLATE_VAR_RE = re.compile(r"\$\{\{?\s*(?:secrets\.|vars\.|env\.)?([A-Za-z_][A-Za-z0-9_]*)\s*\}?\}")
# Matches "word:word:rest" (two-level colon prefix like "fastapi:endpoint:POST:/chat")
_COLON_PREFIX_RE = re.compile(r"^[a-z_]+:[a-z_]+:(.+)$")
# Matches "word:word" where BOTH sides are pure lowercase letters/underscores (no digits/dashes)
# e.g. "framework:openai_agents" YES, "python:3.12-slim" NO, "fastapi:endpoint" YES
_SINGLE_COLON_WORD_RE = re.compile(r"^[a-z_]+:([a-z_][a-z_]*)$")


def canonicalize_text(value: str) -> str:
    lowered = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", lowered)
    return normalized.strip("_") or "unknown"


def normalize_display_name(name: str, component_type: object) -> str:
    """Convert raw adapter identifiers to human-readable display names.

    Preserves MODEL names exactly. Strips template syntax and colon-prefixed
    namespaces, then converts snake_case to Title Case.
    """
    from .types import ComponentType  # lazy import to avoid circular dependency

    if not name:
        return name

    if component_type == ComponentType.MODEL:
        return name

    # Strip GitHub Actions / Jinja2 template variable syntax
    m = _TEMPLATE_VAR_RE.match(name.strip())
    if m:
        name = m.group(1)

    # Strip colon-prefixed namespace like "framework:openai_agents" → "openai_agents".
    # Skipped for CONTAINER_IMAGE: its display name is a "repo:tag" Docker image
    # ref (e.g. "nginx:alpine"), not a namespace prefix — when the tag happens to
    # be a bare lowercase word, the single-colon-word pattern below would
    # otherwise strip the repo name and leave just the tag ("nginx:alpine" →
    # "alpine"). Only stripped when both sides are pure lowercase words (no
    # digits/dashes) — this also avoids stripping Docker image tags like
    # "python:3.12-slim" or path segments like "GET:/chat".
    if component_type != ComponentType.CONTAINER_IMAGE:
        colon_m = _COLON_PREFIX_RE.match(name)
        if colon_m:
            name = colon_m.group(1)
        else:
            single_m = _SINGLE_COLON_WORD_RE.match(name)
            if single_m:
                name = single_m.group(1)

    # Already human-readable (contains spaces)
    if " " in name:
        return name

    # snake_case → Title Case
    result = name.replace("_", " ").replace("-", " ").title()

    # Fix common acronyms that .title() gets wrong
    _ACRONYM_FIXES = {
        "Fastapi": "FastAPI",
        "Openai": "OpenAI",
        "Faq": "FAQ",
        "Mcp": "MCP",
        "Llm": "LLM",
        "Api": "API",
        "Iam": "IAM",
        "Sql": "SQL",
        "Http": "HTTP",
        "Https": "HTTPS",
        "Oidc": "OIDC",
        "Oauth": "OAuth",
        "Oauth2": "OAuth2",
        "Jwt": "JWT",
    }
    for wrong, right in _ACRONYM_FIXES.items():
        result = re.sub(r"\b" + wrong + r"\b", right, result)

    return result

