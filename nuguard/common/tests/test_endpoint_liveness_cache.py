"""Tests for enriched-SBOM caching of endpoint liveness results (Phase 3a).

A fresh cached ``operational``/``liveness_checked_at`` result on a node's
metadata must be reused as-is (no live ping), and an all-cache-hit pass must
never write to the enriched SBOM — only a freshly-probed result should
trigger a persist.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from nuguard.common.endpoint_liveness import check_endpoint_liveness
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

_NS = uuid.NAMESPACE_URL


class _FakeClient:
    def __init__(self, responses: dict[str, tuple[int, str, dict]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[str] = []

    async def invoke_endpoint(
        self,
        path: str,
        method: str = "POST",
        body: dict | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        strip_auth: bool = False,
    ) -> tuple[int, str, dict]:
        self.calls.append(path)
        return self.responses.get(path, (200, "OK", {}))


def _node(path: str, **meta_kwargs: Any) -> Node:
    return Node(
        id=uuid.uuid5(_NS, f"API_ENDPOINT/{path}"),
        name=path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.9,
        metadata=NodeMetadata(endpoint=path, method="GET", **meta_kwargs),
    )


def _sbom(*nodes: Node) -> AiSbomDocument:
    return AiSbomDocument(target="./app", nodes=list(nodes))


@pytest.mark.asyncio
async def test_fresh_cache_skips_live_probe() -> None:
    node = _node(
        "/api/status",
        operational=True,
        liveness_checked_at=datetime.now(timezone.utc).isoformat(),
    )
    sbom = _sbom(node)
    client = _FakeClient({"/api/status": (500, "should not be hit", {})})

    report = await check_endpoint_liveness(sbom, client, ttl_seconds=3600.0)

    assert client.calls == []
    assert report.cached == 1
    assert report.checked == 0
    assert node.metadata.operational is True


@pytest.mark.asyncio
async def test_stale_cache_triggers_reprobe() -> None:
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    node = _node("/api/status", operational=True, liveness_checked_at=stale_ts)
    sbom = _sbom(node)
    client = _FakeClient({"/api/status": (404, "now dead", {})})

    report = await check_endpoint_liveness(sbom, client, ttl_seconds=3600.0)

    assert client.calls == ["/api/status"]
    assert report.checked == 1
    assert report.cached == 0
    assert node.metadata.operational is False


@pytest.mark.asyncio
async def test_empty_liveness_result_does_not_overwrite_cached_data(tmp_path) -> None:
    fresh_ts = datetime.now(timezone.utc).isoformat()
    node = _node("/api/status", operational=True, liveness_checked_at=fresh_ts)
    sbom = _sbom(node)
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(sbom.model_dump_json())
    client = _FakeClient()

    calls: list[Any] = []
    import nuguard.common.endpoint_liveness as liveness_mod

    def _spy_persist(sbom_arg, path_arg):
        calls.append((sbom_arg, path_arg))
        return path_arg

    real_persist = None
    import nuguard.common.auto_sbom_enricher as enricher_mod

    real_persist = enricher_mod.persist_liveness_sbom
    enricher_mod.persist_liveness_sbom = _spy_persist  # type: ignore[assignment]
    try:
        report = await check_endpoint_liveness(
            sbom, client, ttl_seconds=3600.0, sbom_path=sbom_path
        )
    finally:
        enricher_mod.persist_liveness_sbom = real_persist  # type: ignore[assignment]

    assert report.cached == 1
    assert calls == []


@pytest.mark.asyncio
async def test_fresh_probe_result_persisted_to_enriched_sbom(tmp_path) -> None:
    import json

    node = _node("/api/status")
    sbom = _sbom(node)
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(sbom.model_dump_json())
    client = _FakeClient({"/api/status": (200, "OK", {})})

    await check_endpoint_liveness(sbom, client, ttl_seconds=3600.0, sbom_path=sbom_path)

    enriched_path = sbom_path.with_name("app.sbom.enriched.json")
    assert enriched_path.exists()
    written = json.loads(enriched_path.read_text())
    written_node = next(
        n for n in written["nodes"] if n["metadata"]["endpoint"] == "/api/status"
    )
    assert written_node["metadata"]["operational"] is True


@pytest.mark.asyncio
async def test_ttl_none_preserves_original_always_probe_behavior() -> None:
    node = _node(
        "/api/status",
        operational=True,
        liveness_checked_at=datetime.now(timezone.utc).isoformat(),
    )
    sbom = _sbom(node)
    client = _FakeClient({"/api/status": (200, "OK", {})})

    report = await check_endpoint_liveness(sbom, client)  # ttl_seconds=None (default)

    assert client.calls == ["/api/status"]
    assert report.checked == 1
    assert report.cached == 0
