"""Shared JSON / Markdown parsing utilities.

Used by the behavior module, the redteam LLM engine, and any other component
that receives LLM responses that may be wrapped in Markdown code fences.
"""
from __future__ import annotations

import json
import re


def strip_markdown_fences(text: str) -> str:
    """Remove leading/trailing triple-backtick code fences from *text*.

    Handles both plain ```` ``` ```` and language-annotated ```` ```json ```` fences.
    Returns *text* unchanged when no fences are present.
    """
    cleaned = re.sub(r"^```(?:\w+)?\s*", "", text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned.strip(), flags=re.MULTILINE).strip()
    return cleaned


def extract_json_object(text: str) -> dict | None:
    """Return the first JSON object found in *text*, or ``None`` on parse failure.

    Strips Markdown code fences first, then locates the outermost ``{...}``
    block.  Logs nothing — callers are responsible for their own warning on
    ``None``.
    """
    cleaned = strip_markdown_fences(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None


def parse_json_array(text: str) -> list | None:
    """Return a parsed JSON array from *text*, or ``None`` on failure.

    Strips Markdown fences and tries ``json.loads`` on the full cleaned text.
    Falls back to extracting quoted strings of length ≥ 10 as a last resort
    so that partially structured LLM output still yields usable items.

    Returns a (possibly empty) list, or ``None`` when nothing could be parsed.
    """
    cleaned = strip_markdown_fences(text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list):
                    return v
    except json.JSONDecodeError:
        pass
    found = re.findall(r'"([^"]{10,})"', cleaned)
    return found if found else None
