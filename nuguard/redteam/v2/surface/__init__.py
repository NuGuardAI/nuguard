"""Phase 2 — attack-surface graph, recon, and per-target catalog cache.

* :class:`AttackSurface` normalizes the SBOM into the design's attack-surface
  table (wrapping ``AnalysisGraph`` + ``CapabilityDetector``).
* :func:`run_recon` resolves the chat endpoint and extracts authenticated
  user data for realistic prompts/canaries.
* :func:`build_target_catalog` matches KB techniques × surface × policy clauses
  and caches the result keyed by SBOM+policy hash.
"""
from __future__ import annotations

from nuguard.redteam.v2.surface.attack_surface import (
    AttackSurface,
    SurfaceCategory,
    SurfaceNode,
    TrustZone,
)
from nuguard.redteam.v2.surface.recon import (
    ReconResult,
    extract_user_data,
    resolve_chat_endpoint,
    run_recon,
)
from nuguard.redteam.v2.surface.target_catalog import (
    TargetCatalog,
    build_target_catalog,
    compute_policy_hash,
    compute_sbom_hash,
)

__all__ = [
    "AttackSurface",
    "ReconResult",
    "SurfaceCategory",
    "SurfaceNode",
    "TargetCatalog",
    "TrustZone",
    "build_target_catalog",
    "compute_policy_hash",
    "compute_sbom_hash",
    "extract_user_data",
    "resolve_chat_endpoint",
    "run_recon",
]
