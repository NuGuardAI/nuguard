"""LLM-response parsing for :class:`~nuguard.redteam.llm_engine.prompt_generator.LLMPromptGenerator`.

Split out of ``prompt_generator.py`` — these functions turn raw text back
from the redteam LLM into structured turn sequences. See
``prompt_builders.py`` for the prompt-construction counterpart.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nuguard.redteam.scenarios.scenario_types import AttackScenario

_LIST_ITEM_START_RE = re.compile(r"^(?:-\s+|\d+\.\s*)")


def _consolidate_list_turns(turns: list[str]) -> list[str]:
    """Merge turns that are bullet/numbered-list continuations of the preceding turn.

    Handles both cases:
    - Continuation lines emitted WITHOUT a TURN prefix (already handled by the
      parser's ``current_from_prefix`` logic, but kept here as a safety net).
    - Continuation lines emitted WITH an explicit ``TURN N:`` prefix, which the
      parser treats as new turns. Content inspection detects these by checking
      whether the current turn is a list item and the previous turn ended with
      either a colon (intro sentence) or another list item.

    Example — both patterns collapse to 1 turn::

        "If you're doing authorized security work, I can help in safer ways, for example by:"
        "- writing a defensive test plan,"          ← list item after colon intro
        "- creating benign evaluation cases,"       ← list item after list item
        "- designing detection rules."              ← list item after list item
    """
    if len(turns) <= 1:
        return turns
    result: list[str] = [turns[0]]
    for turn in turns[1:]:
        if _LIST_ITEM_START_RE.match(turn.lstrip()):
            prev = result[-1]
            prev_last_line = prev.rstrip().rsplit("\n", 1)[-1].rstrip()
            if prev_last_line.endswith(":") or bool(
                _LIST_ITEM_START_RE.match(prev_last_line.lstrip())
            ):
                result[-1] = prev + "\n" + turn
                continue
        result.append(turn)
    return result


def _parse_turn_sequences(raw: str) -> list[list[str]]:
    """Parse multi-turn LLM output into a list of turn sequences.

    Expected format per sequence::

        TURN 1: <text>
        TURN 2: <text>
        TURN 3: <text>

    Sequences are separated by lines containing only ``---``.
    Returns a list where each element is a list of 2-3 turn strings.
    Falls back to treating each non-empty line as a single-turn sequence when
    the structured format is not present.

    Multi-line turns: lines that do NOT start with ``TURN N:`` are treated as
    continuations of the current turn (e.g. bullet points, numbered list items)
    and joined with ``\\n``.  This prevents a single logical user message that
    contains a bulleted list from being split into multiple separate turns.
    """
    _turn_re = re.compile(r"^TURN\s+\d+\s*:", re.IGNORECASE)
    _turn_strip_re = re.compile(r"^TURN\s+\d+\s*:\s*", re.IGNORECASE)

    sequences: list[list[str]] = []
    blocks = raw.strip().split("---")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        turns: list[str] = []
        current_lines: list[str] = []
        # Track whether the current turn was opened by a TURN N: prefix.
        # Only TURN-prefixed turns collect continuation lines (bullet points,
        # numbered items).  Lines with no prefix are treated as independent
        # turns for backward compatibility with un-prefixed generators.
        current_from_prefix: bool = False
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if _turn_re.match(stripped):
                # New TURN marker — flush the current turn buffer first
                if current_lines:
                    turns.append("\n".join(current_lines))
                    current_lines = []
                payload = _turn_strip_re.sub("", stripped)
                if payload:
                    current_lines = [payload]
                current_from_prefix = True
            else:
                if current_from_prefix and current_lines:
                    # Continuation of a TURN-prefixed turn (bullet, numbered item)
                    current_lines.append(stripped)
                else:
                    # No active TURN-prefixed turn — each line is its own turn
                    if current_lines:
                        turns.append("\n".join(current_lines))
                        current_lines = []
                    current_lines = [stripped]
                    current_from_prefix = False
        if current_lines:
            turns.append("\n".join(current_lines))
        if turns:
            sequences.append(_consolidate_list_turns(turns))

    # Fallback: if no structured sequences found, treat each non-empty line as
    # a single-turn sequence for backward compatibility
    if not sequences:
        for line in raw.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                sequences.append([line])

    return sequences


def _parse_family_response(
    raw: str,
    scenarios: list["AttackScenario"],
) -> dict[str, list[list[str]]]:
    """Parse bulk LLM family response into {scenario_id: [[turns...], ...]}."""
    # Build lookup: normalised title → scenario_id
    title_map: dict[str, str] = {
        s.title.strip().lower(): s.scenario_id for s in scenarios
    }

    result: dict[str, list[list[str]]] = {}

    # Split on "## SCENARIO:" markers
    parts = re.split(r"(?m)^## SCENARIO:\s*", raw)
    for part in parts:
        if not part.strip():
            continue
        # First line is the scenario title; rest is variant blocks separated by "==="
        lines = part.split("\n", 1)
        title_line = lines[0].strip().lower()
        body = lines[1] if len(lines) > 1 else ""

        # Match title to scenario
        scenario_id = title_map.get(title_line)
        if scenario_id is None:
            # Try partial match
            for t, sid in title_map.items():
                if t in title_line or title_line in t:
                    scenario_id = sid
                    break
        if scenario_id is None:
            continue

        # Within this section, split on "===" to get individual scenario sub-blocks
        # (the model might emit === within the section if it didn't follow the format)
        # Then parse each sub-block as turn sequences separated by "---"
        section_body = body.split("===")[0]  # stop at next scenario boundary
        sequences = _parse_turn_sequences(section_body)
        if sequences:
            result[scenario_id] = sequences

    return result
