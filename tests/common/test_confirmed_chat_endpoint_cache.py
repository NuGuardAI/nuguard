"""Regression tests for the runtime-probe-confirmed chat endpoint cache.

Covers two things that used to be silently broken:
1. ``find_confirmed_chat_endpoint`` must find a confirmed endpoint even when
   its path doesn't match SBOM keyword ranking (e.g. "/extract"), since it
   scans every node instead of relying on ranked-candidate selection.
2. ``persist_probe_result_to_sbom`` must clear a stale confirmation from any
   other node when persisting a new one, so at most one endpoint is ever
   trusted as "confirmed" at a time.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from nuguard.common.auto_sbom_enricher import persist_probe_result_to_sbom
from nuguard.common.endpoint_detection.sbom import find_confirmed_chat_endpoint
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType


class _FakeProbeResult:
    def __init__(self, path: str, key: str = "text", is_list: bool = False, value_template=None):
        self.path = path
        self.key = key
        self.is_list = is_list
        self.value_template = value_template


def _confirmed_node(path: str, *, key: str = "text", confirmed_at: str | None = None) -> Node:
    return Node(
        name=f"ANY {path}",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.8,
        metadata=NodeMetadata(
            endpoint=path,
            method="ANY",
            chat_payload_key=key,
            chat_payload_list=False,
            extras={
                "source": "runtime_probe",
                "confirmed_at": confirmed_at or datetime.now(timezone.utc).isoformat(),
            },
        ),
    )


def test_find_confirmed_chat_endpoint_ignores_keyword_ranking() -> None:
    """A confirmed endpoint whose path has no chat-like keywords (e.g.
    "/extract") must still be found by a whole-SBOM scan."""
    sbom = AiSbomDocument(target="./app", nodes=[_confirmed_node("/extract")], edges=[])

    result = find_confirmed_chat_endpoint(sbom)

    assert result == ("/extract", "text", False, None)


def test_find_confirmed_chat_endpoint_respects_expected_path() -> None:
    sbom = AiSbomDocument(target="./app", nodes=[_confirmed_node("/extract")], edges=[])

    assert find_confirmed_chat_endpoint(sbom, expected_path="/extract") is not None
    assert find_confirmed_chat_endpoint(sbom, expected_path="/other") is None


def test_find_confirmed_chat_endpoint_expires_after_ttl() -> None:
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    sbom = AiSbomDocument(
        target="./app", nodes=[_confirmed_node("/extract", confirmed_at=stale_ts)], edges=[]
    )

    assert find_confirmed_chat_endpoint(sbom, ttl_seconds=3600.0) is None
    assert find_confirmed_chat_endpoint(sbom, ttl_seconds=None) is not None


def test_find_confirmed_chat_endpoint_returns_none_without_confirmation() -> None:
    node = Node(
        name="Chat API",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.9,
        metadata=NodeMetadata(endpoint="/chat", method="POST", chat_payload_key="message"),
    )
    sbom = AiSbomDocument(target="./app", nodes=[node], edges=[])

    assert find_confirmed_chat_endpoint(sbom) is None


def test_persist_probe_result_clears_stale_confirmation_on_other_nodes(tmp_path: Path) -> None:
    """When the real endpoint moves (e.g. after a redeploy), persisting a new
    confirmation must clear the old node's marker so the stale endpoint is
    never returned by find_confirmed_chat_endpoint again."""
    import json

    old_confirmed = _confirmed_node("/old-endpoint")
    sbom = AiSbomDocument(target="./app", nodes=[old_confirmed], edges=[])
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text("{}")

    persist_probe_result_to_sbom(_FakeProbeResult("/new-endpoint"), sbom, sbom_path)

    enriched_path = tmp_path / "app.sbom.enriched.json"
    assert enriched_path.exists()
    raw = json.loads(enriched_path.read_text())
    raw.pop("_enrichment_cache_key", None)
    enriched = AiSbomDocument.model_validate(raw)

    confirmed = find_confirmed_chat_endpoint(enriched)
    assert confirmed is not None
    assert confirmed[0] == "/new-endpoint"

    old_node = next(n for n in enriched.nodes if n.metadata and n.metadata.endpoint == "/old-endpoint")
    assert (old_node.metadata.extras or {}).get("source") != "runtime_probe"
