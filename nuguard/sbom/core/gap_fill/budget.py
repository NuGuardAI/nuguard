"""Per-scan budget tracking for the LLM gap-fill discovery pass.

Separate from ``LLMClient.token_counts`` because the client is shared across
all steps of ``AiSbomExtractor._llm_enrich()`` (gap-fill, verification,
MCP-annotate, use-case-summary, IaC-summary, description-enrichment,
relationship-graph, descriptive-names) — token_counts alone can't isolate
"gap-fill's own budget is spent" from "an earlier step already spent tokens."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Rough per-call cost estimate in USD, mirroring verification.py's own
# explicitly-approximate `cost_per_call = 0.001` placeholder — there is no
# per-model pricing table in the codebase today.
_ESTIMATED_COST_PER_CALL = 0.002

DEFAULT_MAX_CALLS = 40
DEFAULT_MAX_COST_USD = 5.0


@dataclass
class GapFillBudget:
    """Tracks LLM call count / estimated cost for one gap-fill run."""

    max_calls: int = DEFAULT_MAX_CALLS
    max_cost_usd: float | None = DEFAULT_MAX_COST_USD
    calls_used: int = 0
    cost_used: float = 0.0
    categories_probed: list[str] = field(default_factory=list)
    exhausted_at: str | None = None

    def can_afford(self, estimated_calls: int = 1) -> bool:
        """Return True if *estimated_calls* more calls fit within budget."""
        if self.exhausted_at is not None:
            return False
        if self.calls_used + estimated_calls > self.max_calls:
            return False
        if self.max_cost_usd is not None:
            projected = self.cost_used + estimated_calls * _ESTIMATED_COST_PER_CALL
            if projected > self.max_cost_usd:
                return False
        return True

    def record(self, calls: int = 1) -> None:
        """Record that *calls* LLM calls were made."""
        self.calls_used += calls
        self.cost_used += calls * _ESTIMATED_COST_PER_CALL

    def mark_exhausted(self, where: str) -> None:
        if self.exhausted_at is None:
            self.exhausted_at = where

    def exhausted(self) -> bool:
        return self.exhausted_at is not None or not self.can_afford(1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls_used": self.calls_used,
            "cost_used_usd": round(self.cost_used, 4),
            "max_calls": self.max_calls,
            "max_cost_usd": self.max_cost_usd,
            "categories_probed": list(self.categories_probed),
            "budget_exhausted": self.exhausted_at is not None,
            "exhausted_at": self.exhausted_at,
        }
