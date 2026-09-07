"""Tests for persisting a discovered pre-scan identity profile into the enriched SBOM.

Regression context: capability discovery already caches its findings in
``<name>.sbom.enriched.json`` across runs, but pre-scan identity discovery
(``DiscoveredProfile``) had no such cache and re-ran live on every invocation
-- wasting turns, and occasionally clobbering a previously-good profile with
an empty one when a single run's discovery turns happened to fail.
"""
from __future__ import annotations

import json

from nuguard.common.auto_sbom_enricher import persist_discovery_profile_sbom
from nuguard.common.discovery import DiscoveredProfile
from nuguard.sbom.models import AiSbomDocument


def _sbom(tmp_path, **kwargs) -> tuple[AiSbomDocument, "object"]:
    sbom = AiSbomDocument(target="./app", **kwargs)
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(sbom.model_dump_json())
    return sbom, sbom_path


def test_persist_discovery_profile_sbom_writes_enriched_artifact(tmp_path):
    sbom, sbom_path = _sbom(tmp_path)
    profile = DiscoveredProfile(customer_name="Asha Patel", ids=["PT-4471"], source="live")
    sbom.discovered_profile = profile.model_dump(mode="json")

    out_path = persist_discovery_profile_sbom(sbom, sbom_path)

    assert out_path == sbom_path.with_name("app.sbom.enriched.json")
    written = json.loads(out_path.read_text())
    assert written["discovered_profile"]["customer_name"] == "Asha Patel"
    assert written["discovered_profile"]["ids"] == ["PT-4471"]
    assert "_enrichment_cache_key" not in written


def test_discovered_profile_round_trips_through_ai_sbom_document(tmp_path):
    profile = DiscoveredProfile(
        customer_name="Asha Patel",
        ids=["PT-4471"],
        entity_map={"patient_id": "PT-4471"},
        raw_response="Here is your record...",
        turns_sent=2,
        source="live",
    )
    sbom = AiSbomDocument(target="./app", discovered_profile=profile.model_dump(mode="json"))

    reloaded = AiSbomDocument.model_validate_json(sbom.model_dump_json())
    reloaded_profile = DiscoveredProfile.model_validate(reloaded.discovered_profile)

    assert reloaded_profile == profile
    assert not reloaded_profile.is_empty


def test_ai_sbom_document_defaults_to_no_discovered_profile():
    sbom = AiSbomDocument(target="./app")
    assert sbom.discovered_profile is None
