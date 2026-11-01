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
from nuguard.common.discovery import (
    DiscoveredProfile,
    cached_discovery_profile,
    profile_cache_fingerprint,
)
from nuguard.sbom.models import AiSbomDocument

_TARGET_URL = "http://target.test"


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


# ---------------------------------------------------------------------------
# cached_discovery_profile — the shared redteam/behavior read-guard (Phase 3b:
# redteam's golden-data cache reuses the same discovered_profile field behavior
# already writes, since DiscoveredProfile.raw_response is already the verbatim
# golden-data text golden_data_filter.py needs — no separate field required).
# ---------------------------------------------------------------------------


def test_cached_discovery_profile_returns_none_when_sbom_is_none():
    assert cached_discovery_profile(None, _TARGET_URL, None) is None


def test_cached_discovery_profile_returns_none_when_field_unset():
    sbom = AiSbomDocument(target="./app")
    assert cached_discovery_profile(sbom, _TARGET_URL, None) is None


def test_cached_discovery_profile_returns_none_when_empty():
    profile = DiscoveredProfile(source="live")
    sbom = AiSbomDocument(
        target="./app",
        discovered_profile=profile.model_dump(mode="json"),
        discovered_profile_fingerprint=profile_cache_fingerprint(_TARGET_URL, None),
    )
    assert cached_discovery_profile(sbom, _TARGET_URL, None) is None


def test_cached_discovery_profile_returns_none_on_unparseable_data():
    sbom = AiSbomDocument(
        target="./app",
        discovered_profile={"ids": "not-a-list"},
        discovered_profile_fingerprint=profile_cache_fingerprint(_TARGET_URL, None),
    )
    assert cached_discovery_profile(sbom, _TARGET_URL, None) is None


# ---------------------------------------------------------------------------
# discovered_profile_fingerprint staleness check (issue #611 Bug 2)
# ---------------------------------------------------------------------------


def test_cached_discovery_profile_returns_none_when_fingerprint_missing():
    """A profile present but with no fingerprint at all (persisted before this
    field existed) is always a cache miss — the core correctness fix: never
    blindly trust a cached profile just because the field is non-empty."""
    profile = DiscoveredProfile(customer_name="Asha Patel", ids=["PT-4471"], source="live")
    sbom = AiSbomDocument(target="./app", discovered_profile=profile.model_dump(mode="json"))
    assert cached_discovery_profile(sbom, _TARGET_URL, None) is None


def test_cached_discovery_profile_returns_none_when_target_url_differs():
    profile = DiscoveredProfile(customer_name="Asha Patel", ids=["PT-4471"], source="live")
    sbom = AiSbomDocument(
        target="./app",
        discovered_profile=profile.model_dump(mode="json"),
        discovered_profile_fingerprint=profile_cache_fingerprint("http://staging.test", None),
    )
    assert cached_discovery_profile(sbom, "http://prod.test", None) is None


def test_cached_discovery_profile_returns_none_when_auth_identity_differs():
    from nuguard.common.auth import AuthConfig

    profile = DiscoveredProfile(customer_name="Asha Patel", ids=["PT-4471"], source="live")
    user_a = AuthConfig(type="bearer", header="Authorization: Bearer token-a")
    user_b = AuthConfig(type="bearer", header="Authorization: Bearer token-b")
    sbom = AiSbomDocument(
        target="./app",
        discovered_profile=profile.model_dump(mode="json"),
        discovered_profile_fingerprint=profile_cache_fingerprint(_TARGET_URL, user_a),
    )
    assert cached_discovery_profile(sbom, _TARGET_URL, user_b) is None


def test_cached_discovery_profile_returns_profile_when_target_and_auth_match():
    profile = DiscoveredProfile(customer_name="Asha Patel", ids=["PT-4471"], source="live")
    sbom = AiSbomDocument(
        target="./app",
        discovered_profile=profile.model_dump(mode="json"),
        discovered_profile_fingerprint=profile_cache_fingerprint(_TARGET_URL, None),
    )
    cached = cached_discovery_profile(sbom, _TARGET_URL, None)
    assert cached is not None
    assert cached.customer_name == "Asha Patel"
    assert cached.ids == ["PT-4471"]


# ---------------------------------------------------------------------------
# profile_cache_fingerprint — type-aware per auth type (issue #611 Bug 2).
# The core correctness fix: must not reuse AuthConfig.header the way
# _enrichment_cache_key does, since that's empty for login_flow auth and
# would make two different login_flow identities indistinguishable.
# ---------------------------------------------------------------------------


def test_fingerprint_differs_for_different_target_urls():
    from nuguard.common.auth import AuthConfig

    auth = AuthConfig(type="bearer", header="Authorization: Bearer tok")
    assert profile_cache_fingerprint("http://a.test", auth) != profile_cache_fingerprint(
        "http://b.test", auth
    )


def test_fingerprint_same_for_same_inputs():
    from nuguard.common.auth import AuthConfig

    auth = AuthConfig(type="bearer", header="Authorization: Bearer tok")
    assert profile_cache_fingerprint(_TARGET_URL, auth) == profile_cache_fingerprint(
        _TARGET_URL, auth
    )


def test_fingerprint_differs_for_different_bearer_tokens():
    from nuguard.common.auth import AuthConfig

    a = AuthConfig(type="bearer", header="Authorization: Bearer token-a")
    b = AuthConfig(type="bearer", header="Authorization: Bearer token-b")
    assert profile_cache_fingerprint(_TARGET_URL, a) != profile_cache_fingerprint(_TARGET_URL, b)


def test_fingerprint_differs_for_different_basic_usernames():
    from nuguard.common.auth import AuthConfig

    a = AuthConfig(type="basic", username="alice", password="pw")
    b = AuthConfig(type="basic", username="bob", password="pw")
    assert profile_cache_fingerprint(_TARGET_URL, a) != profile_cache_fingerprint(_TARGET_URL, b)


def test_fingerprint_differs_for_different_login_flow_identities():
    """The case the naive '_enrichment_cache_key-style, reuse .header'
    approach would get wrong: AuthConfig.header is always empty for
    login_flow, so two different configured identities need to be
    distinguished via the login_flow payload instead."""
    from nuguard.common.auth import AuthConfig, LoginFlowConfig

    user_a = AuthConfig(
        type="login_flow",
        login_flow=LoginFlowConfig(
            endpoint="/login", payload={"username": "alice", "password": "pw-a"}
        ),
    )
    user_b = AuthConfig(
        type="login_flow",
        login_flow=LoginFlowConfig(
            endpoint="/login", payload={"username": "bob", "password": "pw-b"}
        ),
    )
    assert user_a.header == ""
    assert user_b.header == ""  # confirms .header alone can't distinguish them
    assert profile_cache_fingerprint(_TARGET_URL, user_a) != profile_cache_fingerprint(
        _TARGET_URL, user_b
    )


def test_fingerprint_same_for_same_login_flow_identity_across_calls():
    """A fresh AuthConfig built the same way on a later, independent run
    (same configured credentials, different Python object) must produce the
    identical fingerprint — this is what makes cross-run/cross-process
    reuse possible at all."""
    from nuguard.common.auth import AuthConfig, LoginFlowConfig

    def _build() -> AuthConfig:
        return AuthConfig(
            type="login_flow",
            login_flow=LoginFlowConfig(
                endpoint="/login", payload={"username": "alice", "password": "pw-a"}
            ),
        )

    assert profile_cache_fingerprint(_TARGET_URL, _build()) == profile_cache_fingerprint(
        _TARGET_URL, _build()
    )


def test_fingerprint_differs_for_different_cookie_file_paths():
    from nuguard.common.auth import AuthConfig

    a = AuthConfig(type="cookie_file", cookie_file="/tmp/cookies-a.txt")
    b = AuthConfig(type="cookie_file", cookie_file="/tmp/cookies-b.txt")
    assert profile_cache_fingerprint(_TARGET_URL, a) != profile_cache_fingerprint(_TARGET_URL, b)


def test_fingerprint_none_auth_is_stable_and_distinct_from_configured_auth():
    from nuguard.common.auth import AuthConfig

    bearer = AuthConfig(type="bearer", header="Authorization: Bearer tok")
    assert profile_cache_fingerprint(_TARGET_URL, None) == profile_cache_fingerprint(
        _TARGET_URL, None
    )
    assert profile_cache_fingerprint(_TARGET_URL, None) != profile_cache_fingerprint(
        _TARGET_URL, bearer
    )


# ---------------------------------------------------------------------------
# Disk round-trip (issue #611 Phase 2's real scenario: two SEPARATE CLI
# invocations — behavior then redteam, or vice versa, in different processes
# — sharing an on-disk enriched SBOM). Every test above reads/writes the same
# in-memory AiSbomDocument object; these reconstruct a genuinely fresh object
# from the persisted JSON, the way a later, separate process actually would,
# mirroring the same pattern test_enrichment_cache_key_preserved.py already
# uses for the sibling _enrichment_cache_key mechanism.
# ---------------------------------------------------------------------------


def test_discovered_profile_fingerprint_persists_through_enriched_sbom_round_trip(tmp_path):
    sbom, sbom_path = _sbom(tmp_path)
    profile = DiscoveredProfile(customer_name="Asha Patel", ids=["PT-4471"], source="live")
    fingerprint = profile_cache_fingerprint(_TARGET_URL, None)
    sbom.discovered_profile = profile.model_dump(mode="json")
    sbom.discovered_profile_fingerprint = fingerprint

    out_path = persist_discovery_profile_sbom(sbom, sbom_path)

    written = json.loads(out_path.read_text())
    assert written["discovered_profile_fingerprint"] == fingerprint


def test_cached_discovery_profile_hit_after_disk_round_trip(tmp_path):
    """The realistic cross-process scenario: one process discovers and
    persists a profile; a later, separate process loads the enriched SBOM
    fresh off disk and must still get a cache hit for the same target/auth."""
    sbom, sbom_path = _sbom(tmp_path)
    profile = DiscoveredProfile(customer_name="Asha Patel", ids=["PT-4471"], source="live")
    sbom.discovered_profile = profile.model_dump(mode="json")
    sbom.discovered_profile_fingerprint = profile_cache_fingerprint(_TARGET_URL, None)
    out_path = persist_discovery_profile_sbom(sbom, sbom_path)

    fresh_sbom = AiSbomDocument.model_validate_json(out_path.read_text())
    cached = cached_discovery_profile(fresh_sbom, _TARGET_URL, None)

    assert cached is not None
    assert cached.customer_name == "Asha Patel"
    assert cached.ids == ["PT-4471"]


def test_cached_discovery_profile_miss_after_disk_round_trip_with_different_target(tmp_path):
    """Same disk round-trip, but the later process targets a different
    URL — staleness detection must survive the round-trip too, not just
    hold for the original in-memory object."""
    sbom, sbom_path = _sbom(tmp_path)
    profile = DiscoveredProfile(customer_name="Asha Patel", ids=["PT-4471"], source="live")
    sbom.discovered_profile = profile.model_dump(mode="json")
    sbom.discovered_profile_fingerprint = profile_cache_fingerprint(_TARGET_URL, None)
    out_path = persist_discovery_profile_sbom(sbom, sbom_path)

    fresh_sbom = AiSbomDocument.model_validate_json(out_path.read_text())
    cached = cached_discovery_profile(fresh_sbom, "http://different-target.test", None)

    assert cached is None
