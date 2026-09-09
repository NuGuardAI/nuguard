"""Regression test: persisting live-target-derived data (endpoint liveness,
pre-scan discovery profile, capability discovery) must not strip the
enriched artifact's ``_enrichment_cache_key``.

Root cause discovered via live validation of docs/validation-fix.md's
Phase 3 caching: ``persist_liveness_sbom``/``persist_discovery_profile_sbom``/
``persist_capability_discovery_sbom`` wrote with ``cache_key=None``, which
``_write_enriched`` treats as "omit the field entirely" rather than "don't
validate freshness for this write". Once any of them wrote to the enriched
artifact, the file's ``_enrichment_cache_key`` field vanished — so the next
``maybe_auto_enrich_sbom`` call (made by every ``nuguard behavior``/
``nuguard redteam`` invocation) always cache-missed and restarted structural
enrichment from the pristine, never-probed source SBOM, discarding the very
liveness/discovery/capability data these functions were trying to make
reusable across runs. Confirmed live: a behavior run followed immediately by
a redteam run against the same target re-probed every endpoint from scratch
(0 cache hits) instead of reusing the behavior run's results.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nuguard.common.auto_sbom_enricher import (
    maybe_auto_enrich_sbom,
    persist_discovery_profile_sbom,
    persist_liveness_sbom,
)
from nuguard.sbom.models import AiSbomDocument, Node, ScanSummary
from nuguard.sbom.types import ComponentType


def _high_confidence_sbom() -> AiSbomDocument:
    agent = Node(name="Assistant", component_type=ComponentType.AGENT, confidence=0.95)
    endpoint = Node(name="Chat API", component_type=ComponentType.API_ENDPOINT, confidence=0.95)
    endpoint.metadata.endpoint = "/api/chat"
    endpoint.metadata.method = "POST"
    return AiSbomDocument(
        target="unit-test",
        nodes=[agent, endpoint],
        edges=[],
        summary=ScanSummary(),
    )


def test_persist_liveness_preserves_existing_cache_key(tmp_path: Path) -> None:
    sbom = _high_confidence_sbom()
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text("{}", encoding="utf-8")

    # First call establishes the enriched artifact + a real cache key
    # (description generation always runs, even with no LLM configured).
    # Snapshot the pristine content (with its node UUIDs) before enrichment
    # mutates it in place, so the "next CLI invocation" below can rebuild the
    # exact same pristine object a fresh process would load from sbom_path's
    # unchanged JSON — real Node ids round-trip through serialization, they
    # aren't regenerated per call like a second _high_confidence_sbom() would.
    pristine_json = sbom.model_dump_json()

    first = asyncio.run(maybe_auto_enrich_sbom(sbom=sbom, sbom_path=sbom_path, target_url=None))
    artifact_path = first.artifact_path
    assert artifact_path is not None and artifact_path.exists()
    original_key = json.loads(artifact_path.read_text()).get("_enrichment_cache_key")
    assert original_key

    # Simulate a liveness pass persisting operational data on top.
    for node in sbom.nodes:
        if node.component_type == ComponentType.API_ENDPOINT:
            node.metadata.operational = True
            node.metadata.liveness_checked_at = "2026-01-01T00:00:00+00:00"
    persist_liveness_sbom(sbom, sbom_path)

    after_liveness = json.loads(artifact_path.read_text())
    assert after_liveness.get("_enrichment_cache_key") == original_key, (
        "persist_liveness_sbom must not strip the enrichment cache key"
    )

    # The next maybe_auto_enrich_sbom call — as every behavior/redteam CLI
    # invocation makes, always feeding it the pristine on-disk sbom_path
    # content, never the previous call's enriched return value — must now
    # cache-HIT and return the sbom carrying the persisted operational data,
    # not restart from the pristine input.
    fresh_pristine = AiSbomDocument.model_validate_json(pristine_json)
    second = asyncio.run(maybe_auto_enrich_sbom(sbom=fresh_pristine, sbom_path=sbom_path, target_url=None))
    assert "enrichment_cache_hit" in second.reasons
    ep = next(n for n in second.sbom.nodes if n.component_type == ComponentType.API_ENDPOINT)
    assert ep.metadata.operational is True


def test_persist_discovery_profile_preserves_existing_cache_key(tmp_path: Path) -> None:
    sbom = _high_confidence_sbom()
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text("{}", encoding="utf-8")

    first = asyncio.run(maybe_auto_enrich_sbom(sbom=sbom, sbom_path=sbom_path, target_url=None))
    artifact_path = first.artifact_path
    assert artifact_path is not None and artifact_path.exists()
    original_key = json.loads(artifact_path.read_text()).get("_enrichment_cache_key")
    assert original_key

    sbom.discovered_profile = {"customer_name": "Alice Johnson", "account_ids": ["ACCT-001"]}
    persist_discovery_profile_sbom(sbom, sbom_path)

    after = json.loads(artifact_path.read_text())
    assert after.get("_enrichment_cache_key") == original_key
    assert after.get("discovered_profile", {}).get("customer_name") == "Alice Johnson"
