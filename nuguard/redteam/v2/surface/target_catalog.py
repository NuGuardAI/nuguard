"""Per-target catalog: match KB techniques × attack surface × policy clauses.

The target catalog is the durable, cacheable selection of *what to test* for a
specific SBOM + policy.  It is derived by:

1. Capability-gating the v1 :data:`SCENARIO_CATALOG` against the target's
   :class:`AppCapabilityProfile` (reusing the same gate as the v1 selector).
2. Selecting the KB technique records applicable to the target (via builder/
   scenario cross-links or surface presence).
3. Enumerating the in-scope Cognitive Policy clauses.

The result is cached to ``<cache_dir>/catalog-<hash>.yaml`` keyed by a combined
SBOM+policy hash.  An unchanged SBOM/policy yields a cache hit (regression /
continuous-monitoring reuse); a change invalidates the cache and rebuilds.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from nuguard.common.logging import get_logger
from nuguard.redteam.catalog.registry import CATALOG_BY_ID, SCENARIO_CATALOG
from nuguard.redteam.v2.knowledge import load_techniques
from nuguard.redteam.v2.surface.attack_surface import AttackSurface
from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)

CATALOG_SCHEMA_VERSION = "1.0"

# Mirrors the v1 selector's per-profile minimum base_impact gate.
_PROFILE_MIN_IMPACT: dict[str, float] = {"ci": 5.0, "standard": 3.0, "full": 0.0}


class TargetCatalog(BaseModel):
    """Cacheable selection of techniques/scenarios/clauses for one target."""

    schema_version: str = CATALOG_SCHEMA_VERSION
    knowledge_base_version: str
    sbom_hash: str
    policy_hash: str
    target_url: str | None = None
    domain: str = "general"
    generated_at: str = ""
    capabilities: list[str] = Field(default_factory=list)
    technique_ids: list[str] = Field(default_factory=list)
    scenario_spec_ids: list[str] = Field(default_factory=list)
    policy_clauses: list[str] = Field(default_factory=list)

    def matches(self, sbom_hash: str, policy_hash: str) -> bool:
        """True when this cached catalog is still valid for the given inputs."""
        return (
            self.schema_version == CATALOG_SCHEMA_VERSION
            and self.sbom_hash == sbom_hash
            and self.policy_hash == policy_hash
        )


# ── Hashing ────────────────────────────────────────────────────────────────────
def compute_sbom_hash(sbom: AiSbomDocument) -> str:
    """Stable hash over the SBOM's nodes and edges (ignores timestamps)."""
    dumped = sbom.model_dump(mode="json")
    payload = {"nodes": dumped.get("nodes", []), "edges": dumped.get("edges", [])}
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def compute_policy_hash(policy: object | None) -> str:
    """Stable hash over a CognitivePolicy (or ``"none"`` when absent)."""
    if policy is None:
        return "none"
    if isinstance(policy, BaseModel):
        canonical = json.dumps(policy.model_dump(mode="json"), sort_keys=True, default=str)
    else:
        canonical = json.dumps(policy, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _combined_key(sbom_hash: str, policy_hash: str) -> str:
    return hashlib.sha256(f"{sbom_hash}:{policy_hash}".encode()).hexdigest()[:16]


# ── Selection ──────────────────────────────────────────────────────────────────
def _applicable_scenario_spec_ids(surface: AttackSurface, scan_profile: str) -> list[str]:
    """Capability-gate the catalog specs for this target (no payload synthesis)."""
    profile = surface.profile
    min_impact = _PROFILE_MIN_IMPACT.get(scan_profile, 0.0)
    selected: list[str] = []
    for spec in SCENARIO_CATALOG:
        if not spec.enabled:
            continue
        if spec.base_impact < min_impact:
            continue
        if not profile.satisfies(spec.required_capabilities):
            continue
        selected.append(spec.id)
    return selected


def _applicable_technique_ids(spec_ids: list[str], surface: AttackSurface) -> list[str]:
    """Select KB techniques applicable to the target.

    A technique is applicable when any of:
    - it is strategy-only (no builder + no scenario links) — always available;
    - one of its ``mapped_scenario_ids`` survived capability gating;
    - its ``builder_key`` is used by an applicable spec.
    """
    spec_id_set = set(spec_ids)
    applicable_builders = {
        CATALOG_BY_ID[sid].resolved_builder_key() for sid in spec_ids if sid in CATALOG_BY_ID
    }
    out: list[str] = []
    for rec in load_techniques():
        strategy_only = rec.builder_key is None and not rec.mapped_scenario_ids
        scenario_hit = any(sid in spec_id_set for sid in rec.mapped_scenario_ids)
        builder_hit = rec.builder_key is not None and rec.builder_key in applicable_builders
        if strategy_only or scenario_hit or builder_hit:
            out.append(rec.id)
    return out


def _policy_clauses(policy: object | None) -> list[str]:
    """Enumerate stable identifiers for the policy clauses in scope."""
    if policy is None:
        return []
    clauses: list[str] = []

    def _add(prefix: str, items: object) -> None:
        seq = items if isinstance(items, (list, tuple)) else ()
        for item in seq:
            label = getattr(item, "value", str(item)).strip()
            if label:
                clauses.append(f"{prefix}:{label}")

    _add("allowed_topic", getattr(policy, "allowed_topics", None))
    _add("restricted_topic", getattr(policy, "restricted_topics", None))
    _add("restricted_action", getattr(policy, "restricted_actions", None))
    _add("hitl_trigger", getattr(policy, "hitl_triggers", None))
    _add("data_classification", getattr(policy, "data_classification", None))
    for cond in getattr(policy, "hitl_tool_conditions", None) or []:
        tool = getattr(cond, "tool", None) or getattr(cond, "tool_name", None) or "tool"
        clauses.append(f"hitl_tool:{tool}")
    if getattr(policy, "rate_limits", None):
        clauses.append("rate_limits")
    # Raw/unknown sections need human review before testing (design guidance);
    # they are surfaced as clauses so planning records them as coverage gaps.
    raw_sections = getattr(policy, "raw_sections", None)
    if isinstance(raw_sections, dict):
        for key in raw_sections:
            clauses.append(f"raw_section:{key}")
    return clauses


# ── Build + cache ───────────────────────────────────────────────────────────────
def build_target_catalog(
    sbom: AiSbomDocument,
    *,
    policy: object | None = None,
    target_url: str | None = None,
    scan_profile: str = "full",
    cache_dir: Path | None = None,
    force_refresh: bool = False,
) -> tuple[TargetCatalog, bool]:
    """Build (or load from cache) the per-target catalog.

    Returns ``(catalog, from_cache)``.  When ``cache_dir`` is set, a valid cache
    file for the current SBOM+policy is reused unless ``force_refresh`` is set.
    """
    from nuguard.redteam.v2.knowledge import KNOWLEDGE_BASE_VERSION

    sbom_hash = compute_sbom_hash(sbom)
    policy_hash = compute_policy_hash(policy)
    cache_path = (
        cache_dir / f"catalog-{_combined_key(sbom_hash, policy_hash)}.yaml"
        if cache_dir
        else None
    )

    if cache_path and cache_path.exists() and not force_refresh:
        cached = _load_cache(cache_path)
        if cached and cached.matches(sbom_hash, policy_hash):
            _log.info("target catalog cache hit: %s", cache_path.name)
            return cached, True
        _log.info("target catalog cache stale/invalid — rebuilding")

    surface = AttackSurface.from_sbom(sbom, policy=policy)
    spec_ids = _applicable_scenario_spec_ids(surface, scan_profile)
    technique_ids = _applicable_technique_ids(spec_ids, surface)

    catalog = TargetCatalog(
        knowledge_base_version=KNOWLEDGE_BASE_VERSION,
        sbom_hash=sbom_hash,
        policy_hash=policy_hash,
        target_url=target_url,
        domain=surface.profile.domain,
        generated_at=datetime.now(UTC).isoformat(),
        capabilities=sorted(c.value for c in surface.profile.capabilities),
        technique_ids=sorted(technique_ids),
        scenario_spec_ids=sorted(spec_ids),
        policy_clauses=_policy_clauses(policy),
    )

    if cache_path:
        _write_cache(cache_path, catalog)
        _log.info(
            "target catalog built: %d techniques, %d scenarios, %d policy clauses → %s",
            len(catalog.technique_ids),
            len(catalog.scenario_spec_ids),
            len(catalog.policy_clauses),
            cache_path.name,
        )
    return catalog, False


def _load_cache(path: Path) -> TargetCatalog | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return TargetCatalog.model_validate(raw)
    except Exception as exc:  # corrupt cache must not be fatal
        _log.warning("ignoring unreadable target-catalog cache %s: %s", path.name, exc)
        return None


def _write_cache(path: Path, catalog: TargetCatalog) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(catalog.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
