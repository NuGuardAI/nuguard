"""LLM gap-fill discovery pass for AI SBOM extraction.

This package implements Step 0 of the LLM enrichment pipeline: a targeted,
multi-round discovery pass that looks for component types that are absent
or under-represented in the deterministic (AST + regex) results.

See ``rounds.py`` for the round-by-round algorithm, ``gating.py`` for which
categories run and why, ``budget.py`` for the per-scan call/cost cap, and
``dedup.py`` for how newly-discovered nodes are checked against existing ones.

Integration
-----------
Called from ``AiSbomExtractor._llm_enrich()`` **before** ``verify_uncertain_nodes``
so that newly discovered nodes enter the standard verification queue.

Example usage::

    budget = GapFillBudget(max_calls=config.gap_fill_max_calls,
                            max_cost_usd=config.gap_fill_max_cost_usd)
    new_nodes, budget = await discover_missing_nodes(
        doc, file_contents, gap_client, budget=budget,
        enable_privilege=config.gap_fill_enable_privilege,
        enable_guardrail=config.gap_fill_enable_guardrail,
    )
    doc = apply_discovery_results(doc, new_nodes)
"""

from __future__ import annotations

from .budget import GapFillBudget
from .gating import GateReason, _identify_absent_categories, identify_gated_categories
from .rounds import apply_discovery_results, discover_missing_nodes

__all__ = [
    "GapFillBudget",
    "GateReason",
    "apply_discovery_results",
    "discover_missing_nodes",
    "identify_gated_categories",
    "_identify_absent_categories",
]
