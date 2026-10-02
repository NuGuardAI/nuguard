"""Tests for W10 dual-path tool exposure (nuguard/redteam/scenarios/dual_path.py)."""
from __future__ import annotations

import uuid

from nuguard.models.exploit_chain import ExploitStep
from nuguard.redteam.catalog.capability import CapabilityDetector
from nuguard.redteam.catalog.selector import select_scenarios
from nuguard.redteam.executor.executor import StepResult
from nuguard.redteam.scenarios.dual_path import build_dual_path_read, compare_dual_path
from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata
from nuguard.sbom.types import ComponentType, RelationshipType

_NS = uuid.UUID("00000000-0000-0000-0000-000000000003")


def _uuid(name: str) -> uuid.UUID:
    return uuid.uuid5(_NS, name)


def _result(response: str, http_status_code: int | None, target_path: str | None) -> StepResult:
    step = ExploitStep(
        step_id="s", step_type="INVOKE", description="x", payload="x",
        target_path=target_path,
    )
    return StepResult(step=step, response=response, tool_calls=[], http_status_code=http_status_code)


def test_chat_refused_direct_2xx_is_gate_bypass() -> None:
    chat = _result("I cannot share that information.", None, target_path=None)
    direct = _result('{"account": "data"}', 200, target_path="/api/accounts/1")
    assert compare_dual_path(chat, direct) == "gate_bypass"


def test_direct_non_2xx_is_direct_blocked_even_if_chat_refused() -> None:
    chat = _result("I cannot share that information.", None, target_path=None)
    direct = _result("Forbidden", 403, target_path="/api/accounts/1")
    assert compare_dual_path(chat, direct) == "direct_blocked"


def test_both_succeed_is_consistent_not_bypass() -> None:
    chat = _result("Here is the account data.", None, target_path=None)
    direct = _result('{"account": "data"}', 200, target_path="/api/accounts/1")
    assert compare_dual_path(chat, direct) == "consistent"


def test_build_dual_path_read_produces_two_independent_steps() -> None:
    scenario = build_dual_path_read(
        "agent-1", "Support Agent", "get_account", "/api/accounts/1",
        http_method="GET", sensitive_fields=["email"],
    )
    assert scenario.chain is not None
    steps = scenario.chain.steps
    assert len(steps) == 2
    chat_step, direct_step = steps
    assert chat_step.target_path is None
    assert direct_step.target_path == "/api/accounts/1"
    assert direct_step.http_method == "GET"
    assert direct_step.sensitive_fields == ["email"]
    # No dependency between them — both run unconditionally.
    assert chat_step.depends_on == []
    assert direct_step.depends_on == []
    # Neither step's own success should independently inflate other tiers —
    # the dual_path verdict in _build_findings is the finding.
    assert chat_step.contributes_to_finding is False
    assert direct_step.contributes_to_finding is False


def test_x01_generated_end_to_end_from_sbom() -> None:
    agent = Node(
        id=_uuid("agent"), name="support_agent", component_type=ComponentType.AGENT,
        confidence=0.9, metadata=NodeMetadata(),
    )
    tool = Node(
        id=_uuid("tool"), name="get_account", component_type=ComponentType.TOOL,
        confidence=0.9, metadata=NodeMetadata(description="Retrieve account data"),
    )
    endpoint = Node(
        id=_uuid("endpoint"), name="GET /api/accounts/{id}",
        component_type=ComponentType.API_ENDPOINT, confidence=0.9,
        metadata=NodeMetadata(endpoint="/api/accounts/{id}", method="GET"),
    )
    sbom = AiSbomDocument(
        target="unit-test", nodes=[agent, tool, endpoint],
        edges=[Edge(source=agent.id, target=tool.id, relationship_type=RelationshipType.CALLS)],
    )
    profile = CapabilityDetector(sbom).build()
    scenarios, _coverage = select_scenarios(sbom, profile, scan_profile="full")

    x01_scenarios = [s for s in scenarios if s.catalog_id == "X01"]
    assert x01_scenarios, "X01 should be generated once DIRECT_TOOL_ENDPOINT is detected"
    assert all(s.dual_path for s in x01_scenarios)


def test_x01_skips_a_write_method_endpoint() -> None:
    agent = Node(
        id=_uuid("agent2"), name="support_agent", component_type=ComponentType.AGENT,
        confidence=0.9, metadata=NodeMetadata(),
    )
    endpoint = Node(
        id=_uuid("endpoint2"), name="POST /api/accounts/{id}",
        component_type=ComponentType.API_ENDPOINT, confidence=0.9,
        metadata=NodeMetadata(endpoint="/api/accounts/{id}", method="POST"),
    )
    tool = Node(
        id=_uuid("tool2"), name="update_account", component_type=ComponentType.TOOL,
        confidence=0.9, metadata=NodeMetadata(description="Update account data"),
    )
    sbom = AiSbomDocument(
        target="unit-test", nodes=[agent, tool, endpoint],
        edges=[Edge(source=agent.id, target=tool.id, relationship_type=RelationshipType.CALLS)],
    )
    profile = CapabilityDetector(sbom).build()
    scenarios, _coverage = select_scenarios(sbom, profile, scan_profile="full")

    assert not [s for s in scenarios if s.catalog_id == "X01"]
