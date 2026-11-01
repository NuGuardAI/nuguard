"""Decorator registry and weighted selection (redteam-proposal.md W6)."""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

from .base import PayloadDecorator, WeightedDecorator
from .encodings import ENCODING_DECORATORS
from .framing import FRAMING_DECORATORS
from .structure import STRUCTURE_DECORATORS

if TYPE_CHECKING:
    from nuguard.models.exploit_chain import ExploitChain, ExploitStep

# Default weight per decorator name, overridable via
# ``redteam.decorators.weights`` in nuguard.yaml (Phase 5 tunes these from
# run telemetry). Equal weight by default — no a priori reason to prefer
# one encoding over another until we have data.
_DEFAULT_WEIGHT = 1.0

DECORATOR_REGISTRY: tuple[WeightedDecorator, ...] = tuple(
    WeightedDecorator(decorator=d, weight=_DEFAULT_WEIGHT)
    for d in (*ENCODING_DECORATORS, *FRAMING_DECORATORS, *STRUCTURE_DECORATORS)
)


def select_decorator(
    chain: "ExploitChain",
    step: "ExploitStep",
    weights: dict[str, float] | None = None,
    excluded: set[str] | None = None,
    rng: random.Random | None = None,
) -> PayloadDecorator | None:
    """Weighted-sample one applicable decorator, or None if none is eligible.

    ``excluded`` is the set of decorator names already tried on this step —
    the executor hook applies at most one decorator per step, so an empty
    registry-after-filtering (every applicable decorator already tried, or
    none are applicable) returns ``None`` and the caller falls back to
    free-form LLM paraphrase.
    """
    excluded = excluded or set()
    weight_overrides = weights or {}
    candidates = [
        wd for wd in DECORATOR_REGISTRY
        if wd.decorator.name not in excluded and wd.decorator.applies(chain, step)
    ]
    if not candidates:
        return None
    rng = rng or random.Random()
    resolved_weights = [weight_overrides.get(wd.decorator.name, wd.weight) for wd in candidates]
    if sum(resolved_weights) <= 0:
        return None
    [chosen] = rng.choices(candidates, weights=resolved_weights, k=1)
    return chosen.decorator
