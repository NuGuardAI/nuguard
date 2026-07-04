"""Capability-aware red-team scenario catalog.

This package encodes the stable-ID scenario catalog described in
``docs/llm-runs/Red-team-new-design.md``.  It is organised as a declarative
spec registry plus builder factories:

* :mod:`taxonomy` — closed value sets (categories, channels, sinks, evidence,
  safe-execution modes, app capabilities) used across all specs.
* :mod:`spec` — the :class:`~nuguard.redteam.catalog.spec.ScenarioSpec`
  dataclass, one declarative unit per catalog ID.
* :mod:`registry` — ``SCENARIO_CATALOG``: every spec (D/C/T/A/I/M/P/G/J/E/B/K).
* :mod:`builders` — ``BUILDER_FACTORIES``: maps each spec's ``builder_key`` to a
  factory that synthesises the concrete ``AttackScenario`` objects.

The *spec* is the selection + metadata + coverage layer; the *factories* remain
the payload-production layer (so dynamic, SBOM-parameterised payloads are kept).
"""
from __future__ import annotations

from .spec import ScenarioSpec
from .taxonomy import (
    Capability,
    DeliveryChannel,
    EvidenceType,
    SafeExecution,
    ScenarioCategory,
    SinkType,
    SourceTrust,
)

__all__ = [
    "Capability",
    "DeliveryChannel",
    "EvidenceType",
    "SafeExecution",
    "ScenarioCategory",
    "ScenarioSpec",
    "SinkType",
    "SourceTrust",
]
