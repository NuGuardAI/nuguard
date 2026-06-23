"""Red-team engine v2 — knowledge-base-driven, phased, layered-evaluation runner.

This package is developed in parallel with the v1 engine (``nuguard/redteam``)
and reuses its shared infrastructure (target client, canary scanner, policy
engine, LLM client, SBOM/graph models, and the scenario catalog).  v1 remains
the default engine until v2 reaches parity; select v2 with
``nuguard redteam --engine v2`` or ``redteam.engine: v2`` in ``nuguard.yaml``.

See ``docs/llm-runs/redteam-v2-implementation-plan.md`` for the phased design.

Phase 0 (this commit) ships only the scaffolding: the package layout, config
plumbing, the CLI ``--engine`` switch, and a :class:`RedteamV2Orchestrator`
stub that wires the engine end-to-end while producing no findings yet.
"""
from __future__ import annotations

from nuguard.redteam.v2.orchestrator import RedteamV2Orchestrator, RedteamV2Result

__all__ = ["RedteamV2Orchestrator", "RedteamV2Result"]
