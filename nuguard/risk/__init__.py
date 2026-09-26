"""Neutral, engine-agnostic risk aggregation shared by Redteam and Pentest."""
from __future__ import annotations

from .aggregation import aggregate_score, highest_severity

__all__ = ["aggregate_score", "highest_severity"]
