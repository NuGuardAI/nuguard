"""Shared utility helpers for the behavior package.

Centralises small, frequently-duplicated operations so each caller does not
maintain its own diverged copy.

JSON / Markdown parsing helpers (``strip_markdown_fences``, ``extract_json_object``,
``parse_json_array``) are re-exported from :mod:`nuguard.common.json_utils` so that
the redteam module and other consumers can import from a single canonical location.
"""
from __future__ import annotations

import re

from nuguard.common.json_utils import (  # noqa: F401 — re-exported for existing callers
    extract_json_object,
    parse_json_array,
    strip_markdown_fences,
)


def normalise_name(name: str) -> str:
    """Return a canonical form of *name* for fuzzy duplicate detection.

    Strips spaces, underscores, and hyphens and lowercases so that
    ``'Ad Copy Writer'``, ``'AdCopyWriter'``, and ``'ad_copy_writer'``
    all map to the same key.
    """
    return re.sub(r"[\s_\-]+", "", (name or "")).lower()


# ---------------------------------------------------------------------------
# "Not used" / "could be used" patterns
# ---------------------------------------------------------------------------

_NOT_USED_RE = re.compile(
    r"(?:was|is|were|did|does)\s+not\s+(?:used|invoked|called|involved|active|part\s+of)|"
    r"wasn'?t\s+(?:used|invoked|called|involved|part\s+of)|"
    r"didn'?t\s+(?:use|invoke|call|involve)|"
    r"no\s+(?:agent|tool)s?(?:\s+or\s+(?:agent|tool)s?)?\s+(?:were|was)\s+(?:used|invoked|called)|"
    r"not\s+(?:currently\s+)?(?:available|configured|active|enabled)|"
    r"doesn'?t\s+(?:use|have|invoke)\s+any",
    re.IGNORECASE,
)

_COULD_BE_USED_RE = re.compile(
    r"could\s+be\s+used\s+to\s+([^.!?\n]+)",
    re.IGNORECASE,
)


def is_not_used_response(response: str, component: str) -> bool:
    """Return True if response says *component* was not used/invoked.

    Checks whether the "not used" pattern appears near the component name,
    or the response generically says no tools/agents were involved.
    """
    if not response:
        return False
    # Check if the component is mentioned at all
    norm_comp = normalise_name(component)
    norm_resp = normalise_name(response)
    if norm_comp not in norm_resp:
        # Component not mentioned — look for generic "no tools were used" patterns
        return bool(_NOT_USED_RE.search(response))
    # Component mentioned — check if negation appears near the mention
    # Find all occurrences of the component name and look for nearby negation
    pattern = re.compile(re.escape(component), re.IGNORECASE)
    for m in pattern.finditer(response):
        # Look at a window of ±100 chars around the component mention
        start = max(0, m.start() - 100)
        end = min(len(response), m.end() + 100)
        window = response[start:end]
        if _NOT_USED_RE.search(window):
            return True
    return False


def extract_could_be_used_action(response: str) -> str | None:
    """Extract the action from a 'could be used to ...' pattern in *response*."""
    m = _COULD_BE_USED_RE.search(response or "")
    if m:
        return m.group(1).strip().rstrip(".,;")
    return None


def mentioned_actively(name: str, response: str) -> bool:
    """Return True if *name* appears in *response* in an active context.

    Returns False when the response explicitly says the component was NOT used,
    wasn't involved, etc.  Uses word-boundary-aware matching so that
    'marketing' does not match 'AdCopyWriter'.
    """
    if not response or not name:
        return False
    # Component must appear in the response at all
    norm = normalise_name(name)
    if norm not in normalise_name(response) and name.lower() not in response.lower():
        return False
    # Check if it's negated
    if is_not_used_response(response, name):
        return False
    return True
