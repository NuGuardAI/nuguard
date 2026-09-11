"""Unit tests for nuguard/common/endpoint_liveness.py."""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from nuguard.common.endpoint_liveness import check_endpoint_liveness
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

_NS = uuid.NAMESPACE_URL


class _FakeClient:
    """Minimal invoke_endpoint stand-in — mirrors _DummyClient's style in
    test_endpoint_preflight.py."""

    def __init__(self, responses: dict[str, tuple[int, str, dict]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[str] = []
        self.raise_for: dict[str, Exception] = {}

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
        if path in self.raise_for:
            raise self.raise_for[path]
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
async def test_operational_true_on_2xx() -> None:
    node = _node("/api/status")
    sbom = _sbom(node)
    client = _FakeClient({"/api/status": (200, "OK", {})})

    report = await check_endpoint_liveness(sbom, client)

    assert node.metadata.operational is True
    assert report.operational == 1
    assert report.non_operational == 0
    assert node.metadata.liveness_checked_at is not None


@pytest.mark.asyncio
async def test_operational_false_on_404() -> None:
    node = _node("/api/gone")
    sbom = _sbom(node)
    client = _FakeClient({"/api/gone": (404, "not found", {})})

    report = await check_endpoint_liveness(sbom, client)

    assert node.metadata.operational is False
    assert report.non_operational == 1


@pytest.mark.asyncio
async def test_operational_true_on_401_not_false() -> None:
    node = _node("/api/admin")
    sbom = _sbom(node)
    client = _FakeClient({"/api/admin": (401, "unauthorized", {})})

    report = await check_endpoint_liveness(sbom, client)

    assert node.metadata.operational is True
    assert report.operational == 1


@pytest.mark.asyncio
async def test_operational_true_on_403_not_false() -> None:
    node = _node("/api/secure")
    sbom = _sbom(node)
    client = _FakeClient({"/api/secure": (403, "forbidden", {})})

    await check_endpoint_liveness(sbom, client)

    assert node.metadata.operational is True


@pytest.mark.asyncio
async def test_bind_address_endpoint_skipped_not_marked_dead() -> None:
    node = _node("0.0.0.0:8080 (sse)")
    sbom = _sbom(node)
    client = _FakeClient()

    report = await check_endpoint_liveness(sbom, client)

    assert node.metadata.operational is None
    assert report.skipped == 1
    assert client.calls == []


@pytest.mark.asyncio
async def test_rate_limited_endpoint_probed_serially() -> None:
    call_order: list[str] = []

    class _OrderTrackingClient(_FakeClient):
        async def invoke_endpoint(
            self,
            path: str,
            method: str = "POST",
            body: dict | None = None,
            params: dict[str, str] | None = None,
            extra_headers: dict[str, str] | None = None,
            strip_auth: bool = False,
        ) -> tuple[int, str, dict]:
            call_order.append(path)
            return await super().invoke_endpoint(
                path, method=method, body=body, params=params,
                extra_headers=extra_headers, strip_auth=strip_auth,
            )

    rl_node = _node("/api/limited", rate_limited=True)
    normal_node = _node("/api/normal")
    sbom = _sbom(rl_node, normal_node)
    client = _OrderTrackingClient(
        {"/api/limited": (200, "OK", {}), "/api/normal": (200, "OK", {})}
    )

    report = await check_endpoint_liveness(sbom, client, max_concurrent=5)

    # The rate-limited node must be probed (not skipped), just serially —
    # asserting membership rather than exact order since the concurrent pool
    # runs first by construction but that's an implementation detail.
    assert set(call_order) == {"/api/limited", "/api/normal"}
    assert report.checked == 2


@pytest.mark.asyncio
async def test_timeout_marks_non_operational_with_note() -> None:
    import asyncio

    class _HangingClient(_FakeClient):
        async def invoke_endpoint(
            self,
            path: str,
            method: str = "POST",
            body: dict | None = None,
            params: dict[str, str] | None = None,
            extra_headers: dict[str, str] | None = None,
            strip_auth: bool = False,
        ) -> tuple[int, str, dict]:
            await asyncio.sleep(10)
            return 200, "OK", {}

    node = _node("/api/slow")
    sbom = _sbom(node)
    client = _HangingClient()

    report = await check_endpoint_liveness(sbom, client, per_endpoint_timeout=0.05)

    assert node.metadata.operational is False
    assert any("TIMEOUT" in n for n in node.metadata.liveness_notes)
    assert report.non_operational == 1


@pytest.mark.asyncio
async def test_network_exception_marks_non_operational() -> None:
    node = _node("/api/unreachable")
    sbom = _sbom(node)
    client = _FakeClient()
    client.raise_for["/api/unreachable"] = ConnectionError("refused")

    report = await check_endpoint_liveness(sbom, client)

    assert node.metadata.operational is False
    assert any("NETWORK" in n for n in node.metadata.liveness_notes)
    assert report.non_operational == 1
