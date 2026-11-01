"""Compatibility re-export — the implementation moved to :mod:`nuguard.risk.aggregation`.

Kept so ``from nuguard.redteam.risk_engine.risk_scorer import ...`` (and
``nuguard.redteam.risk_engine``, which re-exports these names) keeps working
unchanged for existing Redteam callers and tests.
"""
from __future__ import annotations

from nuguard.risk.aggregation import aggregate_score, highest_severity

__all__ = ["aggregate_score", "highest_severity"]
