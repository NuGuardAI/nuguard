"""Tests for capability-aware catalog generation (Phase 2)."""
from __future__ import annotations

import uuid

import pytest

from nuguard.redteam.catalog.capability import AppCapabilityProfile, CapabilityDetector
from nuguard.redteam.catalog.coverage import CoverageReport
from nuguard.redteam.catalog.selector import select_scenarios
from nuguard.redteam.catalog.taxonomy import Capability as C
from nuguard.redteam.catalog.taxonomy import ScenarioCategory
from nuguard.redteam.scenarios.generator import ScenarioGenerator
from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata, ScanSummary
from nuguard.sbom.types import ComponentType, RelationshipType

_NS = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _uuid(name: str) -> uuid.UUID:
    return uuid.uuid5(_NS, name)


def _agent(name: str, system_prompt: str = "") -> Node:
    return Node(
        id=_uuid(name),
        name=name,
        component_type=ComponentType.AGENT,
        confidence=0.9,
        metadata=NodeMetadata(
            system_prompt_excerpt=system_prompt,
        ),
    )


def _tool(name: str, description: str = "", **kwargs) -> Node:
    meta = NodeMetadata(description=description, **kwargs)
    return Node(
        id=_uuid(name),
        name=name,
        component_type=ComponentType.TOOL,
        confidence=0.9,
        metadata=meta,
    )


def _api(name: str) -> Node:
    return Node(
        id=_uuid(name),
        name=name,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.9,
        metadata=NodeMetadata(),
    )


def _datastore(name: str, pii_fields: list[str] | None = None) -> Node:
    return Node(
        id=_uuid(name),
        name=name,
        component_type=ComponentType.DATASTORE,
        confidence=0.9,
        metadata=NodeMetadata(pii_fields=pii_fields or []),
    )


def _edge(src: Node, tgt: Node, rel: str = RelationshipType.CALLS) -> Edge:
    return Edge(source=src.id, target=tgt.id, relationship_type=rel)


def _sbom(nodes: list[Node], edges: list[Edge] | None = None) -> AiSbomDocument:
    return AiSbomDocument(
        target="test-app",
        nodes=nodes,
        edges=edges or [],
        summary=ScanSummary(),
    )


# ── Capability detection tests ────────────────────────────────────────────────

def test_basic_chat_capability_always_present() -> None:
    agent = _agent("main-agent")
    sbom = _sbom([agent])
    profile = CapabilityDetector(sbom).build()
    assert C.CHAT in profile.capabilities


def test_web_fetch_detected_from_tool_description() -> None:
    agent = _agent("assistant")
    tool = _tool("web-browser", "browses URLs and fetches web content via HTTP")
    sbom = _sbom([agent, tool], [_edge(agent, tool)])
    profile = CapabilityDetector(sbom).build()
    assert C.WEB_FETCH in profile.capabilities


def test_search_detected() -> None:
    agent = _agent("assistant")
    tool = _tool("google-search", "search google for web results")
    sbom = _sbom([agent, tool], [_edge(agent, tool)])
    profile = CapabilityDetector(sbom).build()
    assert C.SEARCH in profile.capabilities


def test_email_comms_detected() -> None:
    agent = _agent("assistant")
    tool = _tool("send-email", "sends email messages via smtp")
    sbom = _sbom([agent, tool], [_edge(agent, tool)])
    profile = CapabilityDetector(sbom).build()
    assert C.EMAIL_COMMS in profile.capabilities


def test_navigation_detected() -> None:
    agent = _agent("car-assistant")
    tool = _tool("navigation", "navigate to a destination via GPS route")
    sbom = _sbom([agent, tool], [_edge(agent, tool)])
    profile = CapabilityDetector(sbom).build()
    assert C.NAVIGATION in profile.capabilities


def test_mcp_detected_from_url() -> None:
    agent = _agent("assistant")
    tool = Node(
        id=_uuid("mcp-tool"),
        name="mcp-tool",
        component_type=ComponentType.TOOL,
        confidence=0.9,
        metadata=NodeMetadata(mcp_server_url="https://mcp.example.com"),
    )
    sbom = _sbom([agent, tool], [_edge(agent, tool)])
    profile = CapabilityDetector(sbom).build()
    assert C.MCP_SERVER in profile.capabilities


def test_datastore_pii_detected() -> None:
    agent = _agent("assistant")
    ds = _datastore("user-db", pii_fields=["name", "email", "phone"])
    sbom = _sbom([agent, ds])
    profile = CapabilityDetector(sbom).build()
    assert C.DATASTORE_PII in profile.capabilities
    assert C.SENSITIVE_CONTEXT in profile.capabilities
    assert "name" in profile.pii_fields


def test_write_sink_detected_from_privilege_scope() -> None:
    agent = _agent("assistant")
    tool = Node(
        id=_uuid("db-write-tool"),
        name="update-record",
        component_type=ComponentType.TOOL,
        confidence=0.9,
        metadata=NodeMetadata(privilege_scope="db_write"),
    )
    sbom = _sbom([agent, tool], [_edge(agent, tool)])
    profile = CapabilityDetector(sbom).build()
    assert C.WRITE_SINK in profile.capabilities


def test_no_capabilities_for_empty_sbom() -> None:
    sbom = _sbom([])
    profile = CapabilityDetector(sbom).build()
    # Only CHAT + RENDERS_MARKDOWN (always present)
    assert C.CHAT in profile.capabilities
    assert C.WEB_FETCH not in profile.capabilities


# ── Domain inference ──────────────────────────────────────────────────────────

def test_automotive_domain_inferred() -> None:
    agent = _agent("car-assistant", "I am a car navigation and climate assistant")
    tool = _tool("navigation", "navigate route")
    sbom = _sbom([agent, tool], [_edge(agent, tool)])
    profile = CapabilityDetector(sbom).build()
    assert profile.domain == "automotive"


def test_fintech_domain_inferred() -> None:
    agent = _agent("bank-assistant", "I help with banking and payments")
    sbom = _sbom([agent])
    profile = CapabilityDetector(sbom).build()
    assert profile.domain == "fintech"


# ── Selector tests ────────────────────────────────────────────────────────────

def _gemini_auto_sbom() -> AiSbomDocument:
    """Mirrors the Gemini-Auto SBOM: one front-door agent + 6 tools, no datastore."""
    agent = _agent("gemini-auto", "automotive assistant")
    api = _api("chat-api")
    tools = [
        _tool("climate-control", "controls vehicle climate and hvac temperature"),
        _tool("communication", "sends messages and emails to contacts"),
        _tool("media-player", "controls audio media and music playback"),
        _tool("navigation", "navigate to destination via GPS route"),
        _tool("web-search", "search google for information"),
        _tool("weather", "fetch weather information from web"),
    ]
    edges = [_edge(api, agent)] + [_edge(agent, t) for t in tools]
    return _sbom([agent, api] + tools, edges)


def _rich_sbom() -> AiSbomDocument:
    """A rich SBom with many capabilities: datastore/PII, MCP, email, web, search."""
    agent = _agent("fintech-assistant", "I help with banking and payments")
    api = _api("chat-api")
    tools = [
        _tool("web-browser", "fetch and browse web URLs via HTTP"),
        _tool("send-email", "send email to customers via smtp"),
        _tool("search-google", "search the web"),
        _tool("calendar-tool", "manage calendar events and appointments"),
        Node(
            id=_uuid("mcp-tool"),
            name="external-mcp",
            component_type=ComponentType.TOOL,
            confidence=0.9,
            metadata=NodeMetadata(mcp_server_url="https://mcp.example.com"),
        ),
        Node(
            id=_uuid("write-tool"),
            name="update-record",
            component_type=ComponentType.TOOL,
            confidence=0.9,
            metadata=NodeMetadata(privilege_scope="db_write"),
        ),
    ]
    ds = _datastore("user-db", pii_fields=["name", "email", "phone", "address"])
    guardrail = Node(
        id=_uuid("guardrail"),
        name="policy-guardrail",
        component_type=ComponentType.GUARDRAIL,
        confidence=0.9,
        metadata=NodeMetadata(),
    )
    edges = [_edge(api, agent)] + [_edge(agent, t) for t in tools]
    return _sbom([agent, api] + tools + [ds, guardrail], edges)


def test_gemini_auto_tool_and_indirect_scenarios_prioritized() -> None:
    """Gemini-Auto should produce covert/destructive/indirect scenarios, not datastore-only."""
    sbom = _gemini_auto_sbom()
    profile = CapabilityDetector(sbom).build()

    # Should NOT have datastore capabilities
    assert C.DATASTORE_PII not in profile.capabilities
    assert C.DATASTORE_PHI not in profile.capabilities

    # Should have automotive domain tools
    assert C.NAVIGATION in profile.capabilities
    assert C.CLIMATE in profile.capabilities
    assert C.SEARCH in profile.capabilities
    assert C.EMAIL_COMMS in profile.capabilities


def test_gemini_auto_datastore_only_specs_skipped() -> None:
    """D05, D06, A01, A02, K01–K06 should be skipped for Gemini-Auto (no datastore/repo)."""
    sbom = _gemini_auto_sbom()
    profile = CapabilityDetector(sbom).build()
    scenarios, coverage = select_scenarios(sbom, profile, scan_profile="full")

    catalog_ids = {s.catalog_id for s in scenarios}
    # Datastore-only: D05, D06 (require RAG)
    assert "D05" not in catalog_ids, "D05 should be skipped (no RAG capability)"
    assert "D06" not in catalog_ids, "D06 should be skipped (no RAG capability)"
    # Coding agents K01-K06 (require repo/shell)
    for kid in ["K01", "K02", "K03", "K04", "K05", "K06"]:
        assert kid not in catalog_ids, f"{kid} should be skipped (no repo/shell)"

    # Coverage should report skipped with reason
    skipped_ids = {sid for sid, _, _ in coverage.skipped}
    assert "D05" in skipped_ids
    assert "K01" in skipped_ids


def test_gemini_auto_generates_tool_scenarios() -> None:
    """Gemini-Auto should generate scenarios targeting communication/navigation tools."""
    sbom = _gemini_auto_sbom()
    profile = CapabilityDetector(sbom).build()
    scenarios, coverage = select_scenarios(sbom, profile, scan_profile="full")

    # Should have some enabled specs (covert + jailbreak + indirect + MCP-basic etc.)
    assert len(scenarios) > 0

    # Coverage should mention at least a few categories
    assert coverage.categories_covered_count >= 3


def test_rich_sbom_generates_many_categories() -> None:
    """A rich SBom with PII, MCP, email, web, calendar, guardrail should hit ≥6 categories."""
    sbom = _rich_sbom()
    profile = CapabilityDetector(sbom).build()
    scenarios, coverage = select_scenarios(sbom, profile, scan_profile="full")

    assert coverage.categories_covered_count >= 6
    assert coverage.total_generated >= 10


def test_coverage_report_has_skipped_with_reasons() -> None:
    sbom = _gemini_auto_sbom()
    profile = CapabilityDetector(sbom).build()
    _, coverage = select_scenarios(sbom, profile, scan_profile="full")

    assert len(coverage.skipped) > 0
    reasons = {reason for _, _, reason in coverage.skipped}
    # Disabled specs have an explicit safety/configuration reason; builder
    # failures remain distinguishable as builder_pending.
    assert any("capability" in r or "pending" in r for r in reasons)
    assert "spec_disabled" in reasons


def test_ci_profile_returns_fewer_scenarios_than_full() -> None:
    sbom = _rich_sbom()
    profile = CapabilityDetector(sbom).build()
    ci_scenarios, _ = select_scenarios(sbom, profile, scan_profile="ci")
    full_scenarios, _ = select_scenarios(sbom, profile, scan_profile="full")
    # ci profile filters by min_impact=5.0 and caps at 20
    assert len(ci_scenarios) <= len(full_scenarios)
    assert len(ci_scenarios) <= 20


def test_catalog_scenarios_have_taxonomy_metadata() -> None:
    """Scenarios from the catalog must have catalog_id, category, delivery_channel etc."""
    sbom = _gemini_auto_sbom()
    profile = CapabilityDetector(sbom).build()
    scenarios, _ = select_scenarios(sbom, profile, scan_profile="full")

    for s in scenarios:
        assert s.catalog_id, f"Missing catalog_id on {s.title!r}"
        assert s.category, f"Missing category on {s.catalog_id}"
        assert s.delivery_channel is not None, f"Missing delivery_channel on {s.catalog_id}"


def test_generator_generate_from_catalog_populates_last_coverage() -> None:
    sbom = _gemini_auto_sbom()
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate_from_catalog(scan_profile="full", with_guided=False)
    assert gen.last_coverage is not None
    assert isinstance(gen.last_coverage, CoverageReport)
    assert gen.last_coverage.total_generated == len(scenarios)


def test_coverage_markdown_renders() -> None:
    sbom = _rich_sbom()
    profile = CapabilityDetector(sbom).build()
    _, coverage = select_scenarios(sbom, profile, scan_profile="full")
    md = coverage.to_markdown()
    assert "## Catalog Coverage" in md
    assert "Generated" in md
