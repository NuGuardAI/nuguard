"""Unit tests for nuguard/behavior/alignment.py."""
from __future__ import annotations

from unittest.mock import MagicMock

from nuguard.behavior.alignment import check_alignment
from nuguard.sbom.models import (
    AiSbomDocument,
    Edge,
    EdgeRelationshipType,
    Node,
    NodeMetadata,
    NodeType,
)


def _make_node(
    name: str,
    node_type: NodeType = NodeType.AGENT,
    metadata: NodeMetadata | None = None,
) -> Node:
    return Node(
        name=name,
        component_type=node_type,
        confidence=1.0,
        metadata=metadata or NodeMetadata(),
    )


def _make_edge(
    source: Node,
    target: Node,
    relationship_type: EdgeRelationshipType = EdgeRelationshipType.CALLS,
) -> Edge:
    return Edge(
        source=source.id,
        target=target.id,
        relationship_type=relationship_type,
    )


def _make_sbom(nodes: list[Node], edges: list[Edge] | None = None) -> AiSbomDocument:
    return AiSbomDocument(target="test", nodes=nodes, edges=edges or [])


def _make_policy(
    restricted_topics: list[str] | None = None,
    restricted_actions: list[str] | None = None,
    hitl_triggers: list[str] | None = None,
):
    policy = MagicMock()
    policy.restricted_topics = restricted_topics or []
    policy.restricted_actions = restricted_actions or []
    policy.hitl_triggers = hitl_triggers or []
    policy.allowed_topics = []
    policy.data_classification = []
    return policy


def _make_intent():
    intent = MagicMock()
    intent.app_purpose = "Marketing AI assistant"
    intent.core_capabilities = ["generate ad copy"]
    return intent


# ---------------------------------------------------------------------------
# BA-001: system prompt restricted topic
# ---------------------------------------------------------------------------


def test_ba_001_detects_restricted_topic():
    agent = _make_node(
        "CopyAgent",
        NodeType.AGENT,
        NodeMetadata(system_prompt_excerpt="You may discuss gambling strategies"),
    )
    sbom = _make_sbom([agent])
    findings = check_alignment(sbom, _make_intent(), _make_policy(restricted_topics=["gambling"]))
    assert any("BA-001" in f.finding_id for f in findings)
    assert any("gambling" in f.title.lower() for f in findings)


def test_ba_001_no_match_without_topic():
    agent = _make_node(
        "CopyAgent",
        NodeType.AGENT,
        NodeMetadata(system_prompt_excerpt="You are a helpful marketing assistant"),
    )
    sbom = _make_sbom([agent])
    findings = check_alignment(sbom, _make_intent(), _make_policy(restricted_topics=["gambling"]))
    assert not any("BA-001" in f.finding_id for f in findings)


def test_ba_001_no_system_prompt_skipped():
    agent = _make_node("CopyAgent", NodeType.AGENT, NodeMetadata())
    sbom = _make_sbom([agent])
    findings = check_alignment(sbom, _make_intent(), _make_policy(restricted_topics=["gambling"]))
    assert not any("BA-001" in f.finding_id for f in findings)


# ---------------------------------------------------------------------------
# BA-002: risky tool no guardrail
# ---------------------------------------------------------------------------


def test_ba_002_risky_tool_no_guardrail():
    tool = _make_node(
        "SQLTool",
        NodeType.TOOL,
        NodeMetadata(sql_injectable=True),
    )
    sbom = _make_sbom([tool])
    findings = check_alignment(sbom, _make_intent(), _make_policy())
    assert any("BA-002" in f.finding_id for f in findings)


def test_ba_002_risky_tool_with_guardrail():
    tool = _make_node("SQLTool", NodeType.TOOL, NodeMetadata(sql_injectable=True))
    guardrail = _make_node("InputValidator", NodeType.GUARDRAIL)
    edge = Edge(source=guardrail.id, target=tool.id, relationship_type=EdgeRelationshipType.PROTECTS)
    sbom = _make_sbom([tool, guardrail], [edge])
    findings = check_alignment(sbom, _make_intent(), _make_policy())
    ba002 = [f for f in findings if "BA-002" in f.finding_id]
    assert ba002 == []


def test_ba_002_safe_tool_no_finding():
    tool = _make_node("SearchTool", NodeType.TOOL, NodeMetadata())
    sbom = _make_sbom([tool])
    findings = check_alignment(sbom, _make_intent(), _make_policy())
    assert not any("BA-002" in f.finding_id for f in findings)


# ---------------------------------------------------------------------------
# BA-003: restricted action tool reachable
# ---------------------------------------------------------------------------


def test_ba_003_restricted_action_reachable():
    agent = _make_node("MarketingAgent", NodeType.AGENT)
    tool = _make_node(
        "MoneyTransferTool",
        NodeType.TOOL,
        NodeMetadata(description="Transfers money between accounts"),
    )
    edge = _make_edge(agent, tool, EdgeRelationshipType.CALLS)
    sbom = _make_sbom([agent, tool], [edge])
    policy = _make_policy(restricted_actions=["money transfer"])
    findings = check_alignment(sbom, _make_intent(), policy)
    assert any("BA-003" in f.finding_id for f in findings)


def test_ba_003_no_restricted_actions_no_finding():
    agent = _make_node("MarketingAgent", NodeType.AGENT)
    tool = _make_node("MoneyTransferTool", NodeType.TOOL)
    edge = _make_edge(agent, tool, EdgeRelationshipType.CALLS)
    sbom = _make_sbom([agent, tool], [edge])
    policy = _make_policy(restricted_actions=[])
    findings = check_alignment(sbom, _make_intent(), policy)
    assert not any("BA-003" in f.finding_id for f in findings)


# ---------------------------------------------------------------------------
# BA-004: PII datastore no guardrail
# ---------------------------------------------------------------------------


def test_ba_004_pii_datastore_no_guardrail():
    ds = _make_node(
        "CustomerDB",
        NodeType.DATASTORE,
        NodeMetadata(pii_fields=["email", "name"]),
    )
    sbom = _make_sbom([ds])
    findings = check_alignment(sbom, _make_intent(), _make_policy())
    assert any("BA-004" in f.finding_id for f in findings)


def test_ba_004_pii_datastore_with_guardrail():
    ds = _make_node("CustomerDB", NodeType.DATASTORE, NodeMetadata(pii_fields=["email"]))
    guardrail = _make_node("DataGuard", NodeType.GUARDRAIL)
    edge = Edge(source=guardrail.id, target=ds.id, relationship_type=EdgeRelationshipType.PROTECTS)
    sbom = _make_sbom([ds, guardrail], [edge])
    findings = check_alignment(sbom, _make_intent(), _make_policy())
    assert not any("BA-004" in f.finding_id for f in findings)


# ---------------------------------------------------------------------------
# BA-005: no-auth agent high-privilege
# ---------------------------------------------------------------------------


def test_ba_005_no_auth_high_privilege():
    agent = _make_node("PublicAgent", NodeType.AGENT, NodeMetadata(no_auth_required=True))
    tool = _make_node("AdminTool", NodeType.TOOL, NodeMetadata(high_privilege=True))
    edge = _make_edge(agent, tool, EdgeRelationshipType.CALLS)
    sbom = _make_sbom([agent, tool], [edge])
    findings = check_alignment(sbom, _make_intent(), _make_policy())
    assert any("BA-005" in f.finding_id for f in findings)


def test_ba_005_auth_agent_no_finding():
    agent = _make_node("SecureAgent", NodeType.AGENT, NodeMetadata(no_auth_required=False))
    tool = _make_node("AdminTool", NodeType.TOOL, NodeMetadata(high_privilege=True))
    edge = _make_edge(agent, tool, EdgeRelationshipType.CALLS)
    sbom = _make_sbom([agent, tool], [edge])
    findings = check_alignment(sbom, _make_intent(), _make_policy())
    assert not any("BA-005" in f.finding_id for f in findings)


# ---------------------------------------------------------------------------
# BA-007: blocked topics gap
# ---------------------------------------------------------------------------


def test_ba_007_blocked_topics_gap():
    agent = _make_node(
        "CopyAgent",
        NodeType.AGENT,
        NodeMetadata(blocked_topics=["sports"]),
    )
    sbom = _make_sbom([agent])
    policy = _make_policy(restricted_topics=["gambling", "violence"])
    findings = check_alignment(sbom, _make_intent(), policy)
    ba007 = [f for f in findings if "BA-007" in f.finding_id]
    assert len(ba007) >= 1
    assert any(
        "gambling" in f.description.lower() or "violence" in f.description.lower()
        for f in ba007
    )


def test_ba_007_no_gap_when_covered():
    agent = _make_node(
        "CopyAgent",
        NodeType.AGENT,
        NodeMetadata(blocked_topics=["gambling", "violence"]),
    )
    sbom = _make_sbom([agent])
    policy = _make_policy(restricted_topics=["gambling"])
    findings = check_alignment(sbom, _make_intent(), policy)
    assert not any("BA-007" in f.finding_id for f in findings)


# ---------------------------------------------------------------------------
# BA-008: no HITL gate
# ---------------------------------------------------------------------------


def test_ba_008_hitl_gate_missing():
    agent = _make_node("CopyAgent", NodeType.AGENT)
    sbom = _make_sbom([agent])
    policy = _make_policy(hitl_triggers=["financial transaction"])
    findings = check_alignment(sbom, _make_intent(), policy)
    assert any("BA-008" in f.finding_id for f in findings)


def test_ba_008_guardrail_covers_trigger():
    guardrail = _make_node(
        "HumanReview",
        NodeType.GUARDRAIL,
        NodeMetadata(description="Requires human review for financial transactions"),
    )
    sbom = _make_sbom([guardrail])
    policy = _make_policy(hitl_triggers=["financial"])
    findings = check_alignment(sbom, _make_intent(), policy)
    ba008 = [f for f in findings if "BA-008" in f.finding_id]
    assert ba008 == []


def test_ba_008_no_triggers_no_finding():
    sbom = _make_sbom([])
    policy = _make_policy(hitl_triggers=[])
    findings = check_alignment(sbom, _make_intent(), policy)
    assert not any("BA-008" in f.finding_id for f in findings)


# ---------------------------------------------------------------------------
# check_alignment: all checks combined
# ---------------------------------------------------------------------------


def test_check_alignment_empty_sbom():
    sbom = _make_sbom([])
    findings = check_alignment(sbom, _make_intent(), _make_policy())
    assert isinstance(findings, list)


def test_check_alignment_returns_findings_list():
    agent = _make_node(
        "CopyAgent",
        NodeType.AGENT,
        NodeMetadata(system_prompt_excerpt="gambling is allowed here"),
    )
    sbom = _make_sbom([agent])
    policy = _make_policy(restricted_topics=["gambling"])
    findings = check_alignment(sbom, _make_intent(), policy)
    assert isinstance(findings, list)
    assert len(findings) > 0
    for f in findings:
        assert f.finding_id
        assert f.title
        assert f.severity
