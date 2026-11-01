"""Tests for the SBOM-backed chat-endpoint resolution cache (issue #611 Phase 3).

validate_and_rotate_chat_endpoint's live preflight (endpoint rotation +
templated path-param bootstrap) had no caching at all — every Target
Verify/Behavior/Redteam invocation re-probed the target from scratch, even
back-to-back runs against the identical target/auth. These tests cover the
new fingerprint-gated cache (endpoint_cache_fingerprint /
cached_endpoint_resolution / persist_endpoint_resolution) in
nuguard.common.endpoint_preflight, mirroring the pattern already proven for
discovered_profile in Phase 2 — plus the config-wins safeguard specific to
this cache: an explicitly configured endpoint must never be silently
overridden by a cached resolution for a different path.
"""
from __future__ import annotations

import json

from nuguard.common.auth import AuthConfig, LoginFlowConfig
from nuguard.common.auto_sbom_enricher import persist_endpoint_resolution_sbom
from nuguard.common.endpoint_preflight import (
    CachedEndpointResolution,
    cached_endpoint_resolution,
    endpoint_cache_fingerprint,
    persist_endpoint_resolution,
)
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

_TARGET_URL = "http://target.test"
_CHAT_PATH = "/conversations/:id/messages"
_SOURCES = {"id": "/conversations"}


def _sbom_with_endpoint(path: str = _CHAT_PATH, sources: "dict[str, str] | None" = _SOURCES) -> AiSbomDocument:
    node = Node(
        name="Chat API",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.95,
        metadata=NodeMetadata(endpoint=path, method="POST", path_param_sources=sources),
    )
    return AiSbomDocument(target="./app", nodes=[node], edges=[])


# ---------------------------------------------------------------------------
# endpoint_cache_fingerprint — type-aware auth + path + declared sources
# ---------------------------------------------------------------------------


def test_fingerprint_same_for_same_inputs():
    auth = AuthConfig(type="bearer", header="Authorization: Bearer tok")
    assert endpoint_cache_fingerprint(_TARGET_URL, auth, _CHAT_PATH, _SOURCES) == endpoint_cache_fingerprint(
        _TARGET_URL, auth, _CHAT_PATH, _SOURCES
    )


def test_legacy_endpoint_cache_fingerprint_requires_revalidation():
    import hashlib
    import json

    from nuguard.common.discovery import auth_identity_string

    sbom = _sbom_with_endpoint()
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None, chat_path=_CHAT_PATH, chat_payload_key="message",
        chat_payload_list=False, chat_response_key=None, endpoint_source="probe", path_param_values={},
    )
    old_value = f"{_TARGET_URL}|{auth_identity_string(None)}|{_CHAT_PATH}|{json.dumps(_SOURCES, sort_keys=True)}"
    sbom.resolved_chat_endpoint_fingerprint = hashlib.sha256(old_value.encode()).hexdigest()
    assert cached_endpoint_resolution(sbom, _TARGET_URL, None) is None


def test_fingerprint_differs_for_different_target_urls():
    assert endpoint_cache_fingerprint("http://a.test", None, _CHAT_PATH, _SOURCES) != endpoint_cache_fingerprint(
        "http://b.test", None, _CHAT_PATH, _SOURCES
    )


def test_fingerprint_differs_for_different_chat_paths():
    assert endpoint_cache_fingerprint(_TARGET_URL, None, "/chat", _SOURCES) != endpoint_cache_fingerprint(
        _TARGET_URL, None, "/api/chat", _SOURCES
    )


def test_fingerprint_differs_for_different_path_param_sources():
    """SBOM regenerated with a different declared creation endpoint for the
    same chat_path, same target/auth — must invalidate the cache (the
    previously-bootstrapped id may no longer be meaningful)."""
    assert endpoint_cache_fingerprint(
        _TARGET_URL, None, _CHAT_PATH, {"id": "/conversations"}
    ) != endpoint_cache_fingerprint(_TARGET_URL, None, _CHAT_PATH, {"id": "/v2/conversations"})


def test_fingerprint_differs_for_different_login_flow_identities():
    """The case a naive header-based fingerprint would get wrong — same as
    profile_cache_fingerprint's equivalent guarantee, reused here via
    auth_identity_string."""
    user_a = AuthConfig(
        type="login_flow",
        login_flow=LoginFlowConfig(endpoint="/login", payload={"username": "alice", "password": "a"}),
    )
    user_b = AuthConfig(
        type="login_flow",
        login_flow=LoginFlowConfig(endpoint="/login", payload={"username": "bob", "password": "b"}),
    )
    assert endpoint_cache_fingerprint(_TARGET_URL, user_a, _CHAT_PATH, _SOURCES) != endpoint_cache_fingerprint(
        _TARGET_URL, user_b, _CHAT_PATH, _SOURCES
    )


def test_fingerprint_treats_none_and_empty_sources_the_same():
    assert endpoint_cache_fingerprint(_TARGET_URL, None, _CHAT_PATH, None) == endpoint_cache_fingerprint(
        _TARGET_URL, None, _CHAT_PATH, {}
    )


# ---------------------------------------------------------------------------
# cached_endpoint_resolution — read-side cache-hit/miss logic
# ---------------------------------------------------------------------------


def test_returns_none_when_sbom_is_none():
    assert cached_endpoint_resolution(None, _TARGET_URL, None) is None


def test_returns_none_when_field_unset():
    sbom = _sbom_with_endpoint()
    assert cached_endpoint_resolution(sbom, _TARGET_URL, None) is None


def test_returns_none_when_fingerprint_missing():
    """A resolution persisted before this field existed is always a miss."""
    sbom = _sbom_with_endpoint()
    sbom.resolved_chat_endpoint = CachedEndpointResolution(chat_path=_CHAT_PATH).model_dump(mode="json")
    assert cached_endpoint_resolution(sbom, _TARGET_URL, None) is None


def test_returns_none_on_unparseable_data():
    sbom = _sbom_with_endpoint()
    sbom.resolved_chat_endpoint = {"chat_path": 12345}  # wrong type, fails validation
    sbom.resolved_chat_endpoint_fingerprint = "whatever"
    assert cached_endpoint_resolution(sbom, _TARGET_URL, None) is None


def test_returns_none_when_target_url_differs():
    sbom = _sbom_with_endpoint()
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path=_CHAT_PATH, chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="sbom", path_param_values={"id": "conv-1"},
    )
    assert cached_endpoint_resolution(sbom, "http://different.test", None) is None


def test_returns_none_when_path_param_sources_changed_on_sbom():
    """The SBOM was regenerated with a different declared creation endpoint
    for the same chat_path — same target/auth, still a cache miss."""
    sbom = _sbom_with_endpoint()
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path=_CHAT_PATH, chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="sbom", path_param_values={"id": "conv-1"},
    )
    # Simulate a re-generated SBOM declaring a different creation endpoint.
    sbom.nodes[0].metadata.path_param_sources = {"id": "/v2/conversations"}
    assert cached_endpoint_resolution(sbom, _TARGET_URL, None) is None


def test_hit_when_target_and_sources_match():
    sbom = _sbom_with_endpoint()
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path=_CHAT_PATH, chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="sbom", path_param_values={"id": "conv-1"},
    )
    hit = cached_endpoint_resolution(sbom, _TARGET_URL, None)
    assert hit is not None
    resolved, params = hit
    assert resolved.chat_path == _CHAT_PATH
    assert params == {"id": "conv-1"}


# ---------------------------------------------------------------------------
# Config-wins safeguard: an explicit endpoint must never be silently
# overridden by a cached resolution for a DIFFERENT path.
# ---------------------------------------------------------------------------


def test_explicit_endpoint_matching_cached_path_is_a_hit():
    sbom = _sbom_with_endpoint()
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path=_CHAT_PATH, chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="config", path_param_values={"id": "conv-1"},
    )
    hit = cached_endpoint_resolution(sbom, _TARGET_URL, None, required_chat_path=_CHAT_PATH)
    assert hit is not None


def test_explicit_endpoint_mismatched_cached_path_is_ignored():
    """The critical safeguard: a cache entry resolved to a DIFFERENT path
    (e.g. from an earlier, non-explicit SBOM-rotated run) must never be used
    to silently override the currently-configured explicit endpoint."""
    sbom = _sbom_with_endpoint(path="/api/v2/chat", sources={})
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path="/api/v2/chat", chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="sbom", path_param_values={},
    )
    hit = cached_endpoint_resolution(sbom, _TARGET_URL, None, required_chat_path="/chat")
    assert hit is None


def test_non_explicit_reuses_cache_regardless_of_which_path_it_resolved_to():
    """No explicit endpoint configured — there is no user-specified path to
    protect, so any fingerprint-valid cached resolution is reused."""
    sbom = _sbom_with_endpoint(path="/api/v2/chat", sources={})
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path="/api/v2/chat", chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="sbom", path_param_values={},
    )
    hit = cached_endpoint_resolution(sbom, _TARGET_URL, None, required_chat_path=None)
    assert hit is not None
    assert hit[0].chat_path == "/api/v2/chat"


# ---------------------------------------------------------------------------
# persist_endpoint_resolution — write-side
# ---------------------------------------------------------------------------


def test_persist_is_noop_when_sbom_is_none():
    persist_endpoint_resolution(
        None, _TARGET_URL, None,
        chat_path=_CHAT_PATH, chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="sbom", path_param_values={},
    )  # must not raise


def test_persist_writes_all_three_fields():
    sbom = _sbom_with_endpoint()
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path=_CHAT_PATH, chat_payload_key="messages", chat_payload_list=True,
        chat_response_key="reply", endpoint_source="probe", path_param_values={"id": "conv-9"},
    )
    assert sbom.resolved_chat_endpoint is not None
    assert sbom.resolved_chat_endpoint["chat_path"] == _CHAT_PATH
    assert sbom.resolved_chat_endpoint["chat_payload_key"] == "messages"
    assert sbom.resolved_chat_endpoint["chat_payload_list"] is True
    assert sbom.resolved_chat_endpoint["chat_response_key"] == "reply"
    assert sbom.resolved_chat_endpoint["endpoint_source"] == "probe"
    assert sbom.resolved_path_param_values == {"id": "conv-9"}
    assert sbom.resolved_chat_endpoint_fingerprint is not None


# ---------------------------------------------------------------------------
# Disk round-trip — the realistic cross-process scenario (two separate CLI
# invocations sharing an on-disk enriched SBOM), mirroring the pattern
# already used for discovered_profile in
# nuguard/common/tests/test_discovery_profile_persistence.py.
# ---------------------------------------------------------------------------


def test_resolution_persists_through_enriched_sbom_round_trip(tmp_path):
    sbom = _sbom_with_endpoint()
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(sbom.model_dump_json())
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path=_CHAT_PATH, chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="sbom", path_param_values={"id": "conv-1"},
    )

    out_path = persist_endpoint_resolution_sbom(sbom, sbom_path)

    written = json.loads(out_path.read_text())
    assert written["resolved_chat_endpoint"]["chat_path"] == _CHAT_PATH
    assert written["resolved_path_param_values"] == {"id": "conv-1"}
    assert written["resolved_chat_endpoint_fingerprint"]


def test_cached_resolution_hit_after_disk_round_trip(tmp_path):
    sbom = _sbom_with_endpoint()
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(sbom.model_dump_json())
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path=_CHAT_PATH, chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="sbom", path_param_values={"id": "conv-1"},
    )
    out_path = persist_endpoint_resolution_sbom(sbom, sbom_path)

    fresh_sbom = AiSbomDocument.model_validate_json(out_path.read_text())
    hit = cached_endpoint_resolution(fresh_sbom, _TARGET_URL, None)

    assert hit is not None
    resolved, params = hit
    assert resolved.chat_path == _CHAT_PATH
    assert params == {"id": "conv-1"}


def test_cached_resolution_miss_after_disk_round_trip_with_different_target(tmp_path):
    sbom = _sbom_with_endpoint()
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(sbom.model_dump_json())
    persist_endpoint_resolution(
        sbom, _TARGET_URL, None,
        chat_path=_CHAT_PATH, chat_payload_key="message", chat_payload_list=False,
        chat_response_key=None, endpoint_source="sbom", path_param_values={"id": "conv-1"},
    )
    out_path = persist_endpoint_resolution_sbom(sbom, sbom_path)

    fresh_sbom = AiSbomDocument.model_validate_json(out_path.read_text())
    hit = cached_endpoint_resolution(fresh_sbom, "http://different-target.test", None)

    assert hit is None
