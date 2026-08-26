"""Multi-round orchestration for the LLM gap-fill discovery pass.

Public entry points: :func:`discover_missing_nodes` / :func:`apply_discovery_results`,
called from ``AiSbomExtractor._llm_enrich()``.

Per gated category, up to three rounds run:

1. **Broad proposal** — one call, same shape as the original single-shot
   gap-fill prompt. Splits results into ``high_confidence`` and
   ``borderline`` (the model's own ``ambiguous`` self-flag). Empty result
   stops here — most category/file combinations have nothing to find.
2. **Targeted follow-up** — only if there's a borderline set and budget
   remains; one batched call re-examines them with a wider code window.
3. **Self-critique** — only for categories in ``self_critique_categories``
   (always PRIVILEGE/GUARDRAIL); an adversarial re-check before any node is
   created, so rejected candidates never enter ``doc.nodes`` at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nuguard.common.logging import get_logger

from ...models import AiSbomDocument, Node
from ...types import ComponentType
from .budget import GapFillBudget
from .categories import _CATEGORY_ORDER
from .dedup import DedupContext
from .gating import GateReason, identify_gated_categories, prompt_excluded_file_probe
from .llm_calls import (
    build_existing_node_summary,
    call_broad_round,
    call_critique_round,
    call_followup_round,
    is_tool_blocklisted,
    result_to_node,
)
from .snippets import build_file_snippets

_log = get_logger(__name__)


@dataclass
class _CategoryRoundState:
    category: ComponentType
    round1_raw: list[dict[str, Any]] = field(default_factory=list)
    high_confidence: list[dict[str, Any]] = field(default_factory=list)
    borderline: list[dict[str, Any]] = field(default_factory=list)
    round2_confirmed: list[dict[str, Any]] = field(default_factory=list)
    round3_survivors: list[dict[str, Any]] = field(default_factory=list)


async def run_category_rounds(
    category: ComponentType,
    reason: GateReason,
    doc: AiSbomDocument,
    file_contents: dict[str, str],
    client: Any,
    budget: GapFillBudget,
    dedup_ctx: DedupContext,
    *,
    self_critique_categories: set[ComponentType],
) -> list[Node]:
    """Run Round 1-3 for one category and return newly-discovered nodes."""
    if not budget.can_afford(1):
        budget.mark_exhausted(f"before-round1:{category.value}")
        return []

    extra_paths: set[str] | None = None
    if category == ComponentType.PROMPT and reason == GateReason.PROBE:
        extra_paths = prompt_excluded_file_probe(doc, file_contents)
        if not extra_paths:
            return []

    snippets = build_file_snippets(category, file_contents, extra_paths=extra_paths)
    if not snippets:
        _log.debug("gap-fill: no relevant files for category=%s", category.value)
        return []

    existing_summary = build_existing_node_summary(doc.nodes)
    state = _CategoryRoundState(category=category)

    try:
        state.round1_raw = await call_broad_round(category, existing_summary, snippets, client)
    except Exception as exc:  # pragma: no cover - defensive, LLMClient already has its own fallback
        _log.warning("gap-fill: round1 LLM call failed for %s: %s", category.value, exc)
        return []
    budget.record(1)
    budget.categories_probed.append(category.value)

    if not state.round1_raw:
        return []

    state.high_confidence = [i for i in state.round1_raw if not i.get("ambiguous")]
    state.borderline = [i for i in state.round1_raw if i.get("ambiguous")]

    if state.borderline:
        if budget.can_afford(1):
            try:
                state.round2_confirmed = await call_followup_round(
                    category, state.borderline, file_contents, client
                )
            except Exception as exc:  # pragma: no cover
                _log.warning("gap-fill: round2 LLM call failed for %s: %s", category.value, exc)
                state.round2_confirmed = []
            budget.record(1)
        else:
            budget.mark_exhausted(f"round2:{category.value}")
            # Precision-preserving default: never silently promote unverified
            # borderline items to nodes just because budget ran out.

    survivors = state.high_confidence + state.round2_confirmed
    needs_critique = category in self_critique_categories

    if needs_critique and survivors:
        if budget.can_afford(1):
            try:
                survivors = await call_critique_round(category, survivors, file_contents, client)
            except Exception as exc:  # pragma: no cover
                _log.warning("gap-fill: round3 LLM call failed for %s: %s", category.value, exc)
                survivors = []  # fail closed for categories that require critique
            budget.record(1)
        else:
            budget.mark_exhausted(f"round3:{category.value}")
            # A category that *requires* self-critique but can't afford it
            # fails closed rather than skipping the safeguard.
            survivors = []

    state.round3_survivors = survivors

    fuzzy = reason == GateReason.PROBE
    new_nodes: list[Node] = []
    for item in survivors:
        if category == ComponentType.TOOL and is_tool_blocklisted(item):
            _log.debug("gap-fill: blocking dev-tool %r", item.get("canonical_name") or item.get("name"))
            continue
        node = result_to_node(item, category)
        if node is None:
            continue
        canonical = str(node.metadata.extras.get("canonical_name", node.name))
        evidence_files = node.metadata.extras.get("evidence_files") or []
        if not dedup_ctx.check_and_register(
            canonical, category, fuzzy=fuzzy, evidence_files=evidence_files
        ):
            _log.debug("gap-fill: skipping duplicate %r", canonical)
            continue
        new_nodes.append(node)
        _log.info(
            "gap-fill: discovered new %s node %r (confidence=%.2f, gate=%s)",
            category.value,
            node.name,
            node.confidence,
            reason.value,
        )
    return new_nodes


async def discover_missing_nodes(
    doc: AiSbomDocument,
    file_contents: dict[str, str],
    llm_client: Any,
    *,
    budget: GapFillBudget | None = None,
    budget_tokens: int | None = None,
    enable_privilege: bool = False,
    enable_guardrail: bool = False,
    self_critique_categories: set[ComponentType] | None = None,
) -> tuple[list[Node], GapFillBudget]:
    """Run the multi-round gap-fill pass and return ``(new_nodes, budget)``.

    Parameters
    ----------
    doc, file_contents, llm_client:
        Same as before the multi-round refactor.
    budget:
        A :class:`GapFillBudget`; a default one is created if omitted.
    budget_tokens:
        Deprecated. Accepted for backward compatibility with the pre-refactor
        single-call-site caller; superseded by *budget* (call/cost based).
    enable_privilege, enable_guardrail:
        Opt-in flags for the two categories excluded by default.
    self_critique_categories:
        Extra categories (beyond the always-forced PRIVILEGE/GUARDRAIL) that
        get a Round 3 self-critique pass.
    """
    if budget is None:
        budget = GapFillBudget()
    if budget_tokens is not None:
        _log.debug("gap-fill: budget_tokens kwarg is deprecated; using GapFillBudget instead")

    critique_categories = set(self_critique_categories or ())
    critique_categories |= {ComponentType.PRIVILEGE, ComponentType.GUARDRAIL}

    decisions = identify_gated_categories(
        doc,
        file_contents,
        enable_privilege=enable_privilege,
        enable_guardrail=enable_guardrail,
    )
    gated = {c: r for c, r in decisions.items() if r != GateReason.SKIPPED}
    if not gated:
        _log.debug("gap-fill: all priority categories present — skipping")
        return [], budget

    _log.info(
        "gap-fill: gated categories: %s",
        {c.value: r.value for c, r in gated.items()},
    )

    dedup_ctx = DedupContext(doc)
    new_nodes: list[Node] = []

    for category in _CATEGORY_ORDER:
        if category not in gated:
            continue
        if budget.exhausted():
            budget.mark_exhausted("scan_budget")
            _log.info("gap-fill: budget exhausted — stopping early")
            break
        reason = gated[category]
        category_nodes = await run_category_rounds(
            category,
            reason,
            doc,
            file_contents,
            llm_client,
            budget,
            dedup_ctx,
            self_critique_categories=critique_categories,
        )
        new_nodes.extend(category_nodes)

    _log.info(
        "gap-fill: %d new node(s) discovered across %d gated categories (calls=%d)",
        len(new_nodes),
        len(gated),
        budget.calls_used,
    )
    return new_nodes, budget


def apply_discovery_results(
    doc: AiSbomDocument,
    new_nodes: list[Node],
) -> AiSbomDocument:
    """Merge *new_nodes* into *doc* and return the updated document.

    Existing nodes are never overwritten. The function updates
    ``doc.summary.node_counts`` to include the new nodes.
    """
    if not new_nodes:
        return doc

    doc.nodes.extend(new_nodes)

    if doc.summary:
        counts: dict[str, int] = {}
        for node in doc.nodes:
            key = node.component_type.value
            counts[key] = counts.get(key, 0) + 1
        doc.summary.node_counts = counts

    return doc
