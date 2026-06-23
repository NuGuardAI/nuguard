"""Phase 1 — versioned technique knowledge base.

Holds the :class:`TechniqueRecord` schema, an ``importlib.resources`` loader with
a pinned :data:`KNOWLEDGE_BASE_VERSION`, and the bundled YAML technique records
under ``data/techniques/``.  Records carry metadata + safe payload *strategy*
(never raw harmful payloads) and cross-link to catalog ``builder_key`` values so
v1 builders synthesise the concrete payloads.
"""
from __future__ import annotations

from nuguard.redteam.v2.knowledge.loader import (
    KNOWLEDGE_BASE_VERSION,
    load_technique_index,
    load_techniques,
    verify_builder_keys,
    verify_scenario_ids,
)
from nuguard.redteam.v2.knowledge.schema import (
    Detector,
    ExecutionMeta,
    StateImpact,
    TechniqueRecord,
)

__all__ = [
    "KNOWLEDGE_BASE_VERSION",
    "Detector",
    "ExecutionMeta",
    "StateImpact",
    "TechniqueRecord",
    "load_technique_index",
    "load_techniques",
    "verify_builder_keys",
    "verify_scenario_ids",
]
