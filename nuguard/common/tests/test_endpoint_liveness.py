"""Unit tests for nuguard/common/endpoint_liveness.py."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from nuguard.common.auto_sbom_enricher import enriched_sbom_artifact_path, persist_liveness_sbom
from nuguard.common.endpoint_liveness import _probe_decision, check_endpoint_liveness
from nuguard.common.endpoint_scenario_gate import should_skip_direct_http_scenario
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


# ── Issue #555 layer 3: a skip-triggered correction must reach disk ────────
# The in-memory reset to operational=None on every skip was always correct,
# but persistence only fired when a *live network probe* happened
# (any_freshly_probed). A sweep whose only work is correcting a stale
# True/False on a mutating endpoint — because every other node is either
# already cache-fresh or also skipped — never triggered a live probe, so the
# correction silently never reached the enriched SBOM on disk. Any later
# process that reads the enriched SBOM directly (a report generator, a
# subsequent run) would keep seeing the stale, incorrect value forever.


def _read_enriched_operational(sbom_path: Path, endpoint_name: str) -> Any:
    raw = json.loads(enriched_sbom_artifact_path(sbom_path).read_text())
    for n in raw["nodes"]:
        if n["name"] == endpoint_name:
            return n["metadata"].get("operational")
    raise AssertionError(f"{endpoint_name!r} not found in persisted SBOM")


@pytest.mark.asyncio
async def test_skip_only_correction_is_persisted_even_with_no_fresh_probe(tmp_path: Path) -> None:
    sbom_path = tmp_path / "fake_app.sbom.json"

    # A stale enriched SBOM on disk: a DELETE endpoint incorrectly marked
    # operational=True by a pre-#555 sweep, alongside a GET endpoint that is
    # already cache-fresh — so this run's sweep has nothing that needs a live
    # network probe at all.
    delete_node = _node("/api/users/me", method="DELETE")
    delete_node.metadata.operational = True
    delete_node.metadata.liveness_checked_at = "2020-01-01T00:00:00+00:00"

    get_node = _node("/api/status", method="GET")
    get_node.metadata.operational = True
    get_node.metadata.liveness_checked_at = datetime.now(timezone.utc).isoformat()

    sbom = _sbom(delete_node, get_node)
    persist_liveness_sbom(sbom, sbom_path)
    assert _read_enriched_operational(sbom_path, "/api/users/me") is True

    client = _FakeClient({"/api/status": (200, "OK", {})})
    report = await check_endpoint_liveness(sbom, client, ttl_seconds=3600.0, sbom_path=sbom_path)

    assert client.calls == []  # no network call for either node
    assert report.checked == 0
    assert delete_node.metadata.operational is None  # in-memory correction (already worked before this fix)
    assert _read_enriched_operational(sbom_path, "/api/users/me") is None  # ...now also on disk


@pytest.mark.asyncio
async def test_no_persist_when_nothing_actually_changed(tmp_path: Path) -> None:
    # A mutating endpoint already at operational=None (the terminal, correct
    # state) plus an already cache-fresh safe endpoint: nothing in this sweep
    # changes, so no write should happen at all — persisting on every no-op
    # sweep would make the sbom_path convenience wrapper needlessly re-write
    # the artifact on every single run forever.
    sbom_path = tmp_path / "fake_app.sbom.json"

    delete_node = _node("/api/users/me", method="DELETE")
    delete_node.metadata.operational = None

    get_node = _node("/api/status", method="GET")
    get_node.metadata.operational = True
    get_node.metadata.liveness_checked_at = datetime.now(timezone.utc).isoformat()

    sbom = _sbom(delete_node, get_node)
    persist_liveness_sbom(sbom, sbom_path)
    written_path = enriched_sbom_artifact_path(sbom_path)
    mtime_before = written_path.stat().st_mtime_ns

    client = _FakeClient({"/api/status": (200, "OK", {})})
    await check_endpoint_liveness(sbom, client, ttl_seconds=3600.0, sbom_path=sbom_path)

    assert written_path.stat().st_mtime_ns == mtime_before  # untouched


@pytest.mark.asyncio
async def test_fresh_probe_alongside_skip_correction_still_persists_both(tmp_path: Path) -> None:
    # Regression guard for the pre-existing (already-working) path: a fresh
    # probe on one node must still trigger persistence of everything,
    # including a skip-correction on an unrelated node in the same sweep.
    sbom_path = tmp_path / "fake_app.sbom.json"

    delete_node = _node("/api/users/me", method="DELETE")
    delete_node.metadata.operational = True
    delete_node.metadata.liveness_checked_at = "2020-01-01T00:00:00+00:00"

    get_node = _node("/api/status", method="GET")  # not cached — will be freshly probed

    sbom = _sbom(delete_node, get_node)
    persist_liveness_sbom(sbom, sbom_path)

    client = _FakeClient({"/api/status": (200, "OK", {})})
    report = await check_endpoint_liveness(sbom, client, ttl_seconds=3600.0, sbom_path=sbom_path)

    assert client.calls == [("GET", "/api/status")]
    assert report.checked == 1
    assert _read_enriched_operational(sbom_path, "/api/users/me") is None
    assert _read_enriched_operational(sbom_path, "/api/status") is True


# ── Issue #555 layer 4: downstream consumer wiring ──────────────────────────
# should_skip_direct_http_scenario is the single decision point both behavior
# and redteam scenario generation use to decide whether to attack an
# endpoint. This proves the actual pipeline connection end to end — real
# check_endpoint_liveness output feeding the real gate function — not just
# each half of the contract verified in isolation (which
# nuguard/common/tests/test_endpoint_scenario_gate.py already covers for the
# gate side, and the tests above cover for the sweep side).


@pytest.mark.asyncio
async def test_mutating_endpoint_never_probed_still_generates_scenarios() -> None:
    node = _node("/api/users/me", method="DELETE")
    sbom = _sbom(node)
    client = _FakeClient({"/api/users/me": (200, "OK", {})})

    await check_endpoint_liveness(sbom, client)

    assert node.metadata.operational is None
    skip, reason = should_skip_direct_http_scenario(node.metadata)
    assert skip is False
    assert reason is None


@pytest.mark.asyncio
async def test_confirmed_dead_safe_endpoint_still_skips_scenarios() -> None:
    # Contrast case: a *safe* endpoint that the sweep actually confirmed dead
    # (operational=False) must still gate scenario generation off, same as
    # before this fix — the fix only changes what happens to mutating
    # endpoints, not the meaning of a confirmed-dead result.
    node = _node("/api/status", method="GET")
    sbom = _sbom(node)
    client = _FakeClient({"/api/status": (404, "not found", {})})

    await check_endpoint_liveness(sbom, client)

    assert node.metadata.operational is False
    skip, reason = should_skip_direct_http_scenario(node.metadata)
    assert skip is True
    assert reason is not None and "non-operational" in reason
