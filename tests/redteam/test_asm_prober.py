"""Tests for the W1 Agentic Surface Model prober (no live network — httpx mocked via respx)."""
from __future__ import annotations

import uuid

import httpx
import pytest
import respx

from nuguard.redteam.enrichment.asm_models import (
    AgenticSurfaceModel,
    AsmCorsFinding,
    AsmEndpoint,
    AsmObservationChannel,
)
from nuguard.redteam.enrichment.asm_prober import (
    DEFAULT_MAX_PROBE_REQUESTS,
    _Budget,
    _BudgetExhausted,
    _probe_inventory,
    _probe_openapi_and_schema,
    _ws_candidate_urls,
    apply_asm_to_sbom,
    build_asm,
    build_asm_findings,
)
from nuguard.redteam.target.client import TargetAppClient
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

BASE = "http://test-app"
_NS = uuid.UUID("00000000-0000-0000-0000-000000000004")


def _uuid(name: str) -> uuid.UUID:
    return uuid.uuid5(_NS, name)


async def _client() -> TargetAppClient:
    return TargetAppClient(base_url=BASE, timeout=5.0)


def _sbom(nodes: list[Node] | None = None) -> AiSbomDocument:
    return AiSbomDocument(target="unit-test", nodes=nodes or [], edges=[])


# ── _Budget ───────────────────────────────────────────────────────────────────


def test_budget_raises_once_exhausted() -> None:
    budget = _Budget(2)
    budget.spend()
    budget.spend()
    with pytest.raises(_BudgetExhausted):
        budget.spend()


# ── _probe_openapi_and_schema ─────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_openapi_probe_classifies_open_and_auth_enforced_and_absent() -> None:
    respx.get(f"{BASE}/openapi.json").mock(return_value=httpx.Response(200, json={"paths": {}}))
    respx.get(f"{BASE}/v1/openapi.json").mock(return_value=httpx.Response(401))
    respx.get(f"{BASE}/api/openapi.json").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/swagger.json").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/swagger/v1/swagger.json").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/docs").mock(return_value=httpx.Response(403))

    client = await _client()
    async with client:
        budget = _Budget(DEFAULT_MAX_PROBE_REQUESTS)
        endpoints = await _probe_openapi_and_schema(client, budget)

    by_url = {e.url: e for e in endpoints}
    assert by_url["/openapi.json"].auth_classification == "open"
    assert by_url["/v1/openapi.json"].auth_classification == "auth_enforced"
    assert by_url["/docs"].auth_classification == "auth_enforced"
    assert "/api/openapi.json" not in by_url  # 404 => absent, not recorded


# ── _probe_inventory ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_inventory_probe_records_disclosure_only_when_open() -> None:
    respx.get(f"{BASE}/api/tools").mock(
        return_value=httpx.Response(200, json={"tools": ["grant_admin_role"]})
    )
    respx.get(f"{BASE}/api/agents").mock(return_value=httpx.Response(403))
    respx.get(f"{BASE}/.well-known/ai-plugin.json").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/.well-known/oauth-protected-resource").mock(return_value=httpx.Response(404))

    client = await _client()
    async with client:
        budget = _Budget(DEFAULT_MAX_PROBE_REQUESTS)
        endpoints, disclosures = await _probe_inventory(client, budget, ())

    by_url = {e.url: e for e in endpoints}
    assert by_url["/api/tools"].auth_classification == "open"
    assert by_url["/api/agents"].auth_classification == "auth_enforced"
    assert len(disclosures) == 1
    assert "grant_admin_role" in str(disclosures[0])


@pytest.mark.asyncio
@respx.mock
async def test_inventory_probe_includes_extra_configured_paths() -> None:
    for path in (
        "/api/tools", "/api/agents", "/.well-known/ai-plugin.json",
        "/.well-known/oauth-protected-resource",
    ):
        respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/api/v2/tools").mock(return_value=httpx.Response(200, json={"tools": []}))

    client = await _client()
    async with client:
        budget = _Budget(DEFAULT_MAX_PROBE_REQUESTS)
        endpoints, disclosures = await _probe_inventory(client, budget, ("/api/v2/tools",))

    assert any(e.url == "/api/v2/tools" for e in endpoints)
    assert len(disclosures) == 1


# ── Budget capping across the whole build_asm pass ───────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_build_asm_respects_max_probe_requests() -> None:
    respx.route(method="GET").mock(return_value=httpx.Response(404))
    respx.route(method="OPTIONS").mock(return_value=httpx.Response(200))

    client = await _client()
    async with client:
        asm = await build_asm(client, _sbom(), max_probe_requests=2)

    assert asm.probe_budget_used <= 2


# ── _ws_candidate_urls ────────────────────────────────────────────────────────


def test_ws_candidate_urls_matches_heuristic_paths() -> None:
    ws_node = Node(
        id=_uuid("ws"), name="agent-logs-stream", component_type=ComponentType.API_ENDPOINT,
        confidence=0.9, metadata=NodeMetadata(endpoint="/ws/agent-logs"),
    )
    rest_node = Node(
        id=_uuid("rest"), name="get-accounts", component_type=ComponentType.API_ENDPOINT,
        confidence=0.9, metadata=NodeMetadata(endpoint="/api/accounts"),
    )
    sbom = _sbom([ws_node, rest_node])

    candidates = _ws_candidate_urls(sbom, BASE)

    assert len(candidates) == 1
    assert candidates[0] == (str(ws_node.id), "/ws/agent-logs")


# ── CORS reflection classification ───────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_probe_cors_detects_wildcard_reflection_with_credentials() -> None:
    respx.options(f"{BASE}/").mock(
        return_value=httpx.Response(
            200,
            headers={
                "Access-Control-Allow-Origin": "https://nuguard-cors-probe.invalid",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Methods": "GET, POST",
            },
        )
    )
    client = await _client()
    async with client:
        headers = await client.probe_cors("/", "https://nuguard-cors-probe.invalid")

    assert headers is not None
    assert headers["access-control-allow-credentials"] == "true"


@pytest.mark.asyncio
@respx.mock
async def test_probe_cors_returns_none_on_transport_error() -> None:
    respx.options(f"{BASE}/").mock(side_effect=httpx.ConnectError("boom"))
    client = await _client()
    async with client:
        headers = await client.probe_cors("/", "https://nuguard-cors-probe.invalid")

    assert headers is None


# ── build_asm_findings ────────────────────────────────────────────────────────


def test_build_asm_findings_covers_w01_through_w04() -> None:
    asm = AgenticSurfaceModel(
        endpoints=[
            AsmEndpoint(url="/api/tools", source="heuristic_inventory", auth_classification="open"),
            AsmEndpoint(url="/openapi.json", source="openapi", auth_classification="open"),
            AsmEndpoint(url="/api/agents", source="heuristic_inventory", auth_classification="auth_enforced"),
        ],
        observation_channels=[
            AsmObservationChannel(url="ws://x/ws", transport="ws", connect_auth_required=False),
        ],
        cors_findings=[
            AsmCorsFinding(url="/", origin_reflected=True, credentials_allowed=True),
        ],
    )
    findings = build_asm_findings(asm)
    titles = "\n".join(f.title for f in findings)

    assert len(findings) == 4  # W01, W02, W03, W04 — auth_enforced endpoint produces nothing
    assert "/api/tools" in titles
    assert "/openapi.json" in titles
    assert "ws://x/ws" in titles
    assert any("CORS" in f.title for f in findings)


def test_build_asm_findings_empty_for_fully_gated_surface() -> None:
    asm = AgenticSurfaceModel(
        endpoints=[
            AsmEndpoint(url="/api/tools", source="heuristic_inventory", auth_classification="auth_enforced"),
        ],
        observation_channels=[
            AsmObservationChannel(url="ws://x/ws", transport="ws", connect_auth_required=True),
        ],
        cors_findings=[
            AsmCorsFinding(url="/", origin_reflected=False, credentials_allowed=False),
        ],
    )
    assert build_asm_findings(asm) == []


# ── apply_asm_to_sbom — credential redaction ─────────────────────────────────


def test_apply_asm_to_sbom_promotes_only_booleans_and_counts() -> None:
    agent = Node(
        id=_uuid("agent"), name="support_agent", component_type=ComponentType.AGENT,
        confidence=0.9, metadata=NodeMetadata(),
    )
    sbom = _sbom([agent])
    asm = AgenticSurfaceModel(
        endpoints=[AsmEndpoint(url="/api/tools", source="heuristic_inventory", auth_classification="open")],
        observation_channels=[
            AsmObservationChannel(url="ws://x/ws", transport="ws", connect_auth_required=False),
        ],
        cors_findings=[AsmCorsFinding(url="/", origin_reflected=True, credentials_allowed=True)],
        inventory_disclosures=[{"Authorization": "Bearer super-secret-token-should-never-persist"}],
    )

    result = apply_asm_to_sbom(sbom, asm)

    summary = result.nodes[0].metadata.asm_summary
    assert summary is not None
    assert summary.sibling_endpoint_count == 1
    assert summary.unauthenticated_inventory_exposed is True
    assert summary.observation_channel_unauthenticated is True
    assert summary.cors_wildcard_with_credentials_live is True
    # The raw disclosure body (which could carry a reflected secret) must
    # never be copied onto the committed-SBOM-bound summary.
    dumped = summary.model_dump_json()
    assert "super-secret-token" not in dumped
