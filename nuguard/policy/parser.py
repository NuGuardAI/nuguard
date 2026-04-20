"""Cognitive Policy Markdown parser.

Parses a policy document written in a heading-based Markdown format into a
structured CognitivePolicy model.  No LLM is involved — the parser is
entirely rule-based.

Format expected::

    ## Allowed Topics
    - Finance
    - Customer service

    ## Restricted Topics
    - Politics
    - Medical advice

    ## Rate Limits
    - requests_per_minute: 60
    - tokens_per_day: 100000
"""

from __future__ import annotations

import re

from nuguard.common.logging import get_logger
from nuguard.models.policy import CognitivePolicy

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Heading → field name mapping
# ---------------------------------------------------------------------------

_HEADING_MAP: dict[str, str] = {
    "allowed topics": "allowed_topics",
    "permitted topics": "allowed_topics",
    "restricted topics": "restricted_topics",
    "forbidden topics": "restricted_topics",
    "restricted actions": "restricted_actions",
    "prohibited actions": "restricted_actions",
    "human in the loop": "hitl_triggers",
    "hitl triggers": "hitl_triggers",
    "human approval required": "hitl_triggers",
    "data classification": "data_classification",
    "rate limits": "rate_limits",
}

# Regex that matches a Markdown heading of any depth (# … ######)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)", re.MULTILINE)
# Bullet list item: "- item" or "* item"
_BULLET_RE = re.compile(r"^[-*]\s+(.+)")
# key: value pair (for rate_limits)
_KV_RE = re.compile(r"^(.+?)\s*:\s*(.+)")


def _strip_heading(text: str) -> str:
    """Remove leading # characters and whitespace from a heading line."""
    return text.lstrip("#").strip()


def _extract_bullets(lines: list[str]) -> list[str]:
    """Return all bullet-list items from *lines*."""
    items: list[str] = []
    for line in lines:
        m = _BULLET_RE.match(line.strip())
        if m:
            items.append(m.group(1).strip())
    return items


def _parse_rate_limits(lines: list[str]) -> dict[str, int]:
    """Parse key: value pairs from *lines* into a dict[str, int].

    Only lines that match the ``key: value`` pattern and whose value is a
    valid integer are included.  Non-integer values are logged and skipped.
    """
    result: dict[str, int] = {}
    for line in lines:
        stripped = line.strip().lstrip("-* ").strip()
        m = _KV_RE.match(stripped)
        if m:
            key = m.group(1).strip()
            raw_val = m.group(2).strip()
            try:
                result[key] = int(raw_val)
            except ValueError:
                _log.debug(
                    "rate_limits: could not parse value %r for key %r — skipping",
                    raw_val,
                    key,
                )
    return result


def parse_policy(text: str) -> CognitivePolicy:
    """Parse a Cognitive Policy Markdown document into a CognitivePolicy.

    The algorithm:
    1. Split the document by ``##`` headings.
    2. Normalise each heading to lowercase and look it up in ``_HEADING_MAP``.
    3. Within each section, extract bullet list items.
    4. For ``rate_limits``: parse ``key: value`` pairs.
    5. Store unrecognised sections in ``raw_sections``.

    Args:
        text: Raw Markdown policy text.

    Returns:
        Populated CognitivePolicy instance.
    """
    allowed_topics: list[str] = []
    restricted_topics: list[str] = []
    restricted_actions: list[str] = []
    hitl_triggers: list[str] = []
    data_classification: list[str] = []
    rate_limits: dict[str, int] = {}
    raw_sections: dict[str, list[str]] = {}

    # Split into (heading_text, body_lines) pairs.
    # We treat any heading level (one or more #) as a section delimiter.
    sections: list[tuple[str, list[str]]] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            # Save previous section
            if current_heading is not None:
                sections.append((current_heading, current_lines))
            current_heading = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Flush the last section
    if current_heading is not None:
        sections.append((current_heading, current_lines))

    for heading, body in sections:
        key = heading.lower()
        field = _HEADING_MAP.get(key)

        if field == "allowed_topics":
            allowed_topics.extend(_extract_bullets(body))
        elif field == "restricted_topics":
            restricted_topics.extend(_extract_bullets(body))
        elif field == "restricted_actions":
            restricted_actions.extend(_extract_bullets(body))
        elif field == "hitl_triggers":
            hitl_triggers.extend(_extract_bullets(body))
        elif field == "data_classification":
            data_classification.extend(_extract_bullets(body))
        elif field == "rate_limits":
            rate_limits.update(_parse_rate_limits(body))
        else:
            # Unrecognised section — keep verbatim bullet items (or all lines)
            bullets = _extract_bullets(body)
            raw_sections[heading] = bullets if bullets else [line for line in body if line.strip()]

    return CognitivePolicy(
        allowed_topics=allowed_topics,
        restricted_topics=restricted_topics,
        restricted_actions=restricted_actions,
        hitl_triggers=hitl_triggers,
        data_classification=data_classification,
        rate_limits=rate_limits,
        raw_sections=raw_sections,
    )
