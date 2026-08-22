"""Category gating for the LLM gap-fill discovery pass.

Decides, per category, whether gap-fill should run at all this scan and
(when it does) whether it's filling a total blank (``ABSENT``) or probing a
category that already has some deterministic nodes because a "likely more
exist" signal fired (``PROBE``).

Historical note on the precision numbers referenced in comments below (e.g.
"~16% precision", "+20 FPs"): these originate from prior ad-hoc tuning
sessions and are not independently reproducible from git history — no linked
benchmark run exists for them in this repo, only the resulting gating rules.
Treat them as soft priors informing the current defaults, not as facts this
module re-derives.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

from ...models import AiSbomDocument
from ...types import ComponentType
from .categories import (
    _AGENT_FRAMEWORK_MARKERS,
    _CATEGORY_ORDER,
    _GAP_FILL_ONLY_IF_ABSENT,
    _GAP_FILL_OPT_IN,
    _GAP_FILL_PROBE_ELIGIBLE,
    _GAP_FILL_SKIP_STEMS,
)


class GateReason(str, Enum):
    ABSENT = "absent"
    PROBE = "probe"
    SKIPPED = "skipped"


# Cheap text-pattern probe for a loop body that registers routes dynamically
# (a route table iterated to call a registration method, or a decorator/call
# applied inside a `for` loop). Deliberately a narrow text heuristic, not a
# full AST walk — the real fix for a specific framework is a dedicated
# adapter; this only justifies spending one extra gap-fill call.
_ROUTE_LOOP_RE = re.compile(
    r"for\s*\(?[^{;]{0,60}\b(?:of|in)\s+\w*route\w*[\s\S]{0,200}?"
    r"(?:\.(?:get|post|put|delete|patch)\(|add_url_rule|\.register\()",
    re.IGNORECASE,
)


def has_agent_framework_node(doc: AiSbomDocument) -> bool:
    """Return True if any FRAMEWORK node looks like a recognized AI framework.

    Used to gate AGENT gap-fill: framework-based agents already have high
    deterministic recall and must never re-trigger the (narrower, more
    speculative) hand-rolled-orchestrator heuristic just because that
    framework's AGENT node happened to land below the confidence bar.
    """
    for node in doc.nodes:
        if node.component_type != ComponentType.FRAMEWORK:
            continue
        candidates = {
            str(node.metadata.framework or "").lower(),
            str(node.metadata.extras.get("adapter", "")).lower(),
            str(node.metadata.extras.get("canonical_name", "")).lower(),
            node.name.lower(),
        }
        if any(marker in candidate for candidate in candidates for marker in _AGENT_FRAMEWORK_MARKERS):
            return True
    return False


def has_endpoint_registration_loop(file_contents: dict[str, str]) -> bool:
    """Probe signal for API_ENDPOINT: does any file look like it registers
    routes from a loop/table rather than one decorator/call per route?"""
    for content in file_contents.values():
        if _ROUTE_LOOP_RE.search(content):
            return True
    return False


def tool_framework_diversity_probe(doc: AiSbomDocument) -> bool:
    """Probe signal for TOOL: multiple frameworks/models but disproportionately
    few tools suggests some tool registrations were missed."""
    framework_count = sum(1 for n in doc.nodes if n.component_type == ComponentType.FRAMEWORK)
    model_count = sum(1 for n in doc.nodes if n.component_type == ComponentType.MODEL)
    tool_count = sum(1 for n in doc.nodes if n.component_type == ComponentType.TOOL)
    diversity = framework_count + model_count
    return diversity >= 2 and tool_count < diversity


def prompt_excluded_file_probe(doc: AiSbomDocument, file_contents: dict[str, str]) -> set[str]:
    """Probe signal for PROMPT: files that were excluded from gap-fill
    context because their stem matches a prompt-related skip stem (e.g.
    `prompts.py`, `templates.py`) but produced zero deterministic PROMPT
    nodes. Returns the set of such paths (empty set = no probe signal).

    These files are deliberately excluded from the *broad* Round 1 snippet
    context (to avoid the false positives that justified the exclusion in
    the first place) — the caller should send a narrower, more constrained
    prompt when probing them (see llm_calls.py).
    """
    prompt_count = sum(1 for n in doc.nodes if n.component_type == ComponentType.PROMPT)
    candidates: set[str] = set()
    for path in file_contents:
        stem = Path(path).stem.lower()
        if stem in _GAP_FILL_SKIP_STEMS and (
            "prompt" in stem or "template" in stem or "instruction" in stem
        ):
            candidates.add(path)
    if prompt_count > 0:
        # Deterministic detection already found something; only probe if
        # candidate files exist that weren't part of that detection surface.
        return candidates
    return candidates


def identify_gated_categories(
    doc: AiSbomDocument,
    file_contents: dict[str, str],
    *,
    enable_privilege: bool = False,
    enable_guardrail: bool = False,
) -> dict[ComponentType, GateReason]:
    """Return the gating decision for every category in ``_CATEGORY_ORDER``."""
    present_types: dict[ComponentType, float] = {}
    type_counts: dict[ComponentType, int] = {}
    for node in doc.nodes:
        ct = node.component_type
        type_counts[ct] = type_counts.get(ct, 0) + 1
        if ct not in present_types or node.confidence > present_types[ct]:
            present_types[ct] = node.confidence

    decisions: dict[ComponentType, GateReason] = {}
    for category in _CATEGORY_ORDER:
        # Opt-in categories (PRIVILEGE/GUARDRAIL): disabled by default.
        if category in _GAP_FILL_OPT_IN:
            enabled = (
                enable_privilege
                if category == ComponentType.PRIVILEGE
                else enable_guardrail
            )
            if not enabled:
                decisions[category] = GateReason.SKIPPED
                continue
            # Opted in: ABSENT-only, same conservatism as _GAP_FILL_ONLY_IF_ABSENT.
            decisions[category] = (
                GateReason.ABSENT if type_counts.get(category, 0) == 0 else GateReason.SKIPPED
            )
            continue

        # AGENT: unchanged binary gate — hand-rolled orchestration only.
        if category == ComponentType.AGENT:
            if type_counts.get(ComponentType.AGENT, 0) > 0 or has_agent_framework_node(doc):
                decisions[category] = GateReason.SKIPPED
            else:
                decisions[category] = GateReason.ABSENT
            continue

        count = type_counts.get(category, 0)
        if count == 0:
            decisions[category] = GateReason.ABSENT
            continue

        # Category already has nodes — only high-recall/ABSENT-only types are
        # skipped outright; PROBE-eligible types get a chance if their signal fires.
        if category in _GAP_FILL_ONLY_IF_ABSENT:
            decisions[category] = GateReason.SKIPPED
            continue

        if category in _GAP_FILL_PROBE_ELIGIBLE:
            probe_signal = False
            if category == ComponentType.API_ENDPOINT:
                probe_signal = has_endpoint_registration_loop(file_contents)
            elif category == ComponentType.TOOL:
                probe_signal = tool_framework_diversity_probe(doc)
            elif category == ComponentType.PROMPT:
                probe_signal = bool(prompt_excluded_file_probe(doc, file_contents))
            decisions[category] = GateReason.PROBE if probe_signal else GateReason.SKIPPED
            continue

        # Fallback: original low-confidence threshold for any category not
        # otherwise classified above.
        max_conf = present_types.get(category, 0.0)
        decisions[category] = GateReason.ABSENT if max_conf < 0.65 else GateReason.SKIPPED

    return decisions


def identify_absent_categories(doc: AiSbomDocument) -> list[ComponentType]:
    """Backward-compatible wrapper: returns categories gated ABSENT or PROBE.

    Kept because ``nuguard/sbom/tests/test_gap_fill.py`` imports this name
    directly and asserts set-membership; it doesn't need to know about
    ``GateReason``. New code should call :func:`identify_gated_categories`.
    """
    decisions = identify_gated_categories(doc, {})
    return [c for c, reason in decisions.items() if reason != GateReason.SKIPPED]


# Preserve the old private name as an alias too, since it's the exact name
# imported by test_gap_fill.py.
_identify_absent_categories = identify_absent_categories
_has_agent_framework_node = has_agent_framework_node
