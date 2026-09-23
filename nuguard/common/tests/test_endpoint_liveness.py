"""Unit tests for nuguard/common/endpoint_liveness.py."""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from nuguard.common.endpoint_liveness import _probe_decision, check_endpoint_liveness
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

_NS = uuid.NAMESPACE_URL


class _FakeClient:
    """Minimal invoke_endpoint stand-in — mirrors _DummyClient's style in
    test_endpoint_preflight.py."""

    def __init__(self, responses: dict[str, tuple[int, str, dict]] | None = None) -> None:
        self.responses = responses or {}
        # (method, path) tuples — records both, so tests can assert a
        # mutating method was never sent, not just that some request happened.
        self.calls: list[tuple[str, str]] = []
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
        self.calls.append((method.upper(), path))
        if path in self.raise_for:
            raise self.raise_for[path]
        return self.responses.get(path, (200, "OK", {}))


def _node(path: str, **meta_kwargs: Any) -> Node:
    meta_kwargs.setdefault("method", "GET")
    return Node(
        id=uuid.uuid5(_NS, f"API_ENDPOINT/{path}"),
        name=path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.9,
        metadata=NodeMetadata(endpoint=path, **meta_kwargs),
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


# ── Issue #555: mutating methods are never dynamically probed ──────────────


class TestProbeDecision:
    """Direct unit tests for the pure _probe_decision function — no client,
    no I/O, matching its own design goal of being decidable before any
    network call."""

    def test_get_is_allowed(self) -> None:
        meta = NodeMetadata(endpoint="/api/status", method="GET")
        allowed, reason = _probe_decision(meta)
        assert allowed is True
        assert reason is None

    def test_head_is_allowed(self) -> None:
        meta = NodeMetadata(endpoint="/api/status", method="HEAD")
        assert _probe_decision(meta) == (True, None)

    def test_options_is_allowed(self) -> None:
        meta = NodeMetadata(endpoint="/api/status", method="OPTIONS")
        assert _probe_decision(meta) == (True, None)

    @pytest.mark.parametrize("method", ["DELETE", "POST", "PUT", "PATCH", "delete", "Post"])
    def test_mutating_methods_never_allowed(self, method: str) -> None:
        meta = NodeMetadata(endpoint="/api/users/me", method=method)
        allowed, reason = _probe_decision(meta)
        assert allowed is False
        assert reason is not None and "safe method" in reason

    def test_unresolved_path_param_never_allowed_even_for_get(self) -> None:
        meta = NodeMetadata(endpoint="/api/users/:id", method="GET")
        allowed, reason = _probe_decision(meta)
        assert allowed is False
        assert reason is not None and "path parameter" in reason

    def test_unresolved_path_param_takes_precedence_over_method(self) -> None:
        # A DELETE with an unresolved param should report the path-param
        # reason, not the method reason — order matters for a clear message.
        meta = NodeMetadata(endpoint="/api/users/{id}", method="DELETE")
        allowed, reason = _probe_decision(meta)
        assert allowed is False
        assert reason is not None and "path parameter" in reason

    def test_missing_method_defaults_to_get_and_is_allowed(self) -> None:
        meta = NodeMetadata(endpoint="/api/status", method=None)
        assert _probe_decision(meta) == (True, None)


@pytest.mark.asyncio
async def test_mutating_endpoint_never_probed_stays_none() -> None:
    node = _node("/api/users/me", method="DELETE")
    sbom = _sbom(node)
    client = _FakeClient({"/api/users/me": (200, "OK", {})})

    report = await check_endpoint_liveness(sbom, client)

    assert client.calls == []
    assert node.metadata.operational is None
    assert report.skipped == 1
    assert report.checked == 0
    assert any("safe method" in n for n in node.metadata.liveness_notes)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["DELETE", "POST", "PUT", "PATCH"])
async def test_every_mutating_method_never_sends_a_request(method: str) -> None:
    node = _node("/api/resource", method=method)
    sbom = _sbom(node)
    client = _FakeClient({"/api/resource": (200, "OK", {})})

    await check_endpoint_liveness(sbom, client)

    assert client.calls == []
    assert node.metadata.operational is None


@pytest.mark.asyncio
async def test_unresolved_path_param_never_probed_regardless_of_method() -> None:
    node = _node("/api/users/:id", method="GET")
    sbom = _sbom(node)
    client = _FakeClient({"/api/users/:id": (200, "OK", {})})

    report = await check_endpoint_liveness(sbom, client)

    assert client.calls == []
    assert node.metadata.operational is None
    assert report.skipped == 1


@pytest.mark.asyncio
async def test_liveness_disabled_sends_no_requests_even_for_safe_methods() -> None:
    node = _node("/api/status")  # GET — would normally be probed
    sbom = _sbom(node)
    client = _FakeClient({"/api/status": (200, "OK", {})})

    report = await check_endpoint_liveness(sbom, client, liveness_enabled=False)

    assert client.calls == []
    assert node.metadata.operational is None
    assert report.checked == 0
    assert report.notes and "disabled" in report.notes[0]


@pytest.mark.asyncio
async def test_5xx_on_safe_method_marks_non_operational() -> None:
    node = _node("/api/status")
    sbom = _sbom(node)
    client = _FakeClient({"/api/status": (500, "internal error", {})})

    report = await check_endpoint_liveness(sbom, client)

    assert node.metadata.operational is False
    assert report.non_operational == 1
    assert any("server error" in n for n in node.metadata.liveness_notes)


@pytest.mark.asyncio
async def test_stale_operational_reset_to_none_when_reclassified_as_mutating() -> None:
    # Simulates a node left over from before this fix (or a corrected SBOM
    # re-extraction that changed the declared method): a mutating endpoint
    # must never keep serving a stale True/False from an unsafe prior probe.
    node = _node("/api/users/me", method="DELETE")
    node.metadata.operational = True
    node.metadata.liveness_checked_at = "2020-01-01T00:00:00+00:00"
    sbom = _sbom(node)
    client = _FakeClient({"/api/users/me": (200, "OK", {})})

    report = await check_endpoint_liveness(sbom, client, ttl_seconds=3600.0)

    assert client.calls == []
    assert node.metadata.operational is None
    assert report.cached == 0  # never reached the cache-freshness check at all


@pytest.mark.asyncio
async def test_liveness_notes_populated_for_structurally_skipped_node_too() -> None:
    # Pre-existing skip reason (not an HTTP path) — this test documents that
    # the fix also closes the small pre-existing gap where meta.liveness_notes
    # was only ever populated for actually-probed nodes.
    node = _node("0.0.0.0:8080 (sse)")
    sbom = _sbom(node)
    client = _FakeClient()

    await check_endpoint_liveness(sbom, client)

    assert node.metadata.liveness_notes
    assert "bind address" in node.metadata.liveness_notes[0]
