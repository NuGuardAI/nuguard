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
from nuguard.models.policy import CognitivePolicy, HitlToolCondition
from nuguard.sbom.models import SourceLocation

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
# tool-scoped HITL trigger: single word/identifier followed by colon, e.g. "discount_tool: amount > 10%"
# Requires the left-hand side to be a plausible tool name (word chars, hyphens, spaces ≤ 4 words)
_TOOL_CONDITION_RE = re.compile(r"^([\w][\w\-\.]{0,49})\s*:\s*(.{3,})$")


def _strip_heading(text: str) -> str:
    """Remove leading # characters and whitespace from a heading line."""
    return text.lstrip("#").strip()


def _extract_bullets(lines: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Return all bullet-list items from *lines* as (item_text, line_no) pairs."""
    items: list[tuple[str, int]] = []
    for line, line_no in lines:
        m = _BULLET_RE.match(line.strip())
        if m:
            items.append((m.group(1).strip(), line_no))
    return items


def _parse_hitl_triggers(
    bullets: list[tuple[str, int]],
) -> tuple[list[tuple[str, int]], list[tuple[HitlToolCondition, int]]]:
    """Split HITL bullet items into plain keyword triggers and tool-scoped conditions.

    A bullet is treated as a tool-scoped condition when it matches the pattern
    ``tool_name: condition description``, where ``tool_name`` is a single
    identifier (word chars, hyphens, dots).  All other bullets are treated as
    plain keyword triggers matched against the prompt text at runtime.

    Returns:
        Tuple of (keyword_triggers, tool_conditions), each paired with the
        source line number the bullet was found on.
    """
    keyword_triggers: list[tuple[str, int]] = []
    tool_conditions: list[tuple[HitlToolCondition, int]] = []
    for item, line_no in bullets:
        m = _TOOL_CONDITION_RE.match(item.strip())
        if m:
            tool_name = m.group(1).strip()
            condition = m.group(2).strip()
            tool_conditions.append(
                (HitlToolCondition(tool_name=tool_name, condition=condition), line_no)
            )
            _log.debug(
                "hitl_triggers: parsed tool-scoped condition tool=%r condition=%r",
                tool_name,
                condition,
            )
        else:
            keyword_triggers.append((item, line_no))
    return keyword_triggers, tool_conditions


def _parse_rate_limits(lines: list[tuple[str, int]]) -> dict[str, tuple[int, int]]:
    """Parse key: value pairs from *lines* into a dict[str, (value, line_no)].

    Only lines that match the ``key: value`` pattern and whose value is a
    valid integer are included.  Non-integer values are logged and skipped.
    """
    result: dict[str, tuple[int, int]] = {}
    for line, line_no in lines:
        stripped = line.strip().lstrip("-* ").strip()
        m = _KV_RE.match(stripped)
        if m:
            key = m.group(1).strip()
            raw_val = m.group(2).strip()
            try:
                result[key] = (int(raw_val), line_no)
            except ValueError:
                _log.debug(
                    "rate_limits: could not parse value %r for key %r — skipping",
                    raw_val,
                    key,
                )
    return result


def parse_policy(text: str, source_path: str = "cognitive_policy.md") -> CognitivePolicy:
    """Parse a Cognitive Policy Markdown document into a CognitivePolicy.

    The algorithm:
    1. Split the document by ``##`` headings.
    2. Normalise each heading to lowercase and look it up in ``_HEADING_MAP``.
    3. Within each section, extract bullet list items.
    4. For ``rate_limits``: parse ``key: value`` pairs.
    5. Store unrecognised sections in ``raw_sections``.

    Args:
        text: Raw Markdown policy text.
        source_path: Path recorded in per-item evidence (``item_evidence``),
            typically the policy document's filename.

    Returns:
        Populated CognitivePolicy instance.
    """
    allowed_topics: list[str] = []
    restricted_topics: list[str] = []
    restricted_actions: list[str] = []
    hitl_triggers: list[str] = []
    hitl_tool_conditions: list[HitlToolCondition] = []
    data_classification: list[str] = []
    rate_limits: dict[str, int] = {}
    raw_sections: dict[str, list[str]] = {}
    item_evidence: dict[str, SourceLocation] = {}

    def _record(field: str, item_text: str, line_no: int) -> None:
        item_evidence[f"{field}:{item_text}"] = SourceLocation(
            path=source_path, line=line_no
        )

    # Split into (heading_text, body_lines) pairs, each body line paired with
    # its 1-based line number in the original document.
    # We treat any heading level (one or more #) as a section delimiter.
    sections: list[tuple[str, list[tuple[str, int]]]] = []
    current_heading: str | None = None
    current_lines: list[tuple[str, int]] = []

    for line_no, line in enumerate(text.splitlines(), start=1):
        m = _HEADING_RE.match(line)
        if m:
            # Save previous section
            if current_heading is not None:
                sections.append((current_heading, current_lines))
            current_heading = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append((line, line_no))

    # Flush the last section
    if current_heading is not None:
        sections.append((current_heading, current_lines))

    for heading, body in sections:
        key = heading.lower()
        field = _HEADING_MAP.get(key)

        if field == "allowed_topics":
            for item, line_no in _extract_bullets(body):
                allowed_topics.append(item)
                _record(field, item, line_no)
        elif field == "restricted_topics":
            for item, line_no in _extract_bullets(body):
                restricted_topics.append(item)
                _record(field, item, line_no)
        elif field == "restricted_actions":
            for item, line_no in _extract_bullets(body):
                restricted_actions.append(item)
                _record(field, item, line_no)
        elif field == "hitl_triggers":
            kw, tool_conds = _parse_hitl_triggers(_extract_bullets(body))
            for item, line_no in kw:
                hitl_triggers.append(item)
                _record("hitl_triggers", item, line_no)
            for cond, line_no in tool_conds:
                hitl_tool_conditions.append(cond)
                _record(
                    "hitl_tool_conditions", f"{cond.tool_name}: {cond.condition}", line_no
                )
        elif field == "data_classification":
            for item, line_no in _extract_bullets(body):
                data_classification.append(item)
                _record(field, item, line_no)
        elif field == "rate_limits":
            for key_name, (value, line_no) in _parse_rate_limits(body).items():
                rate_limits[key_name] = value
                _record("rate_limits", f"{key_name}: {value}", line_no)
        else:
            # Unrecognised section — keep verbatim bullet items (or all lines)
            bullets = _extract_bullets(body)
            raw_sections[heading] = (
                [item for item, _ in bullets]
                if bullets
                else [line for line, _ in body if line.strip()]
            )

    return CognitivePolicy(
        allowed_topics=allowed_topics,
        restricted_topics=restricted_topics,
        restricted_actions=restricted_actions,
        hitl_triggers=hitl_triggers,
        hitl_tool_conditions=hitl_tool_conditions,
        data_classification=data_classification,
        rate_limits=rate_limits,
        raw_sections=raw_sections,
        item_evidence=item_evidence,
    )
