"""Test AtlasAnnotatorPlugin MITRE ATLAS technique mapping."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nuguard.models.sbom import (
    AiSbomDocument,
    Edge,
    EdgeRelationshipType,
    Node,
    NodeMetadata,
    NodeType,
    PrivilegeScope,
    ScanSummary,
)
from nuguard.sbom.toolbox.plugins.atlas_annotator import AtlasAnnotatorPlugin


def _doc(**kwargs) -> AiSbomDocument:
    return AiSbomDocument(
        generated_at=datetime.now(timezone.utc),
        target="test",
        summary=ScanSummary(),
        **kwargs,
    )


def _node(name: str, nt: NodeType, **meta_kwargs) -> Node:
    import hashlib
    nid = hashlib.sha256(f"{name}:{nt.value}".encode()).hexdigest()[:8]
    return Node(
        id=nid,
        name=name,
        component_type=nt,
        metadata=NodeMetadata(**meta_kwargs),
    )


@pytest.fixture
def plugin() -> AtlasAnnotatorPlugin:
    return AtlasAnnotatorPlugin()


def test_agent_external_api_maps_t0010(plugin: AtlasAnnotatorPlugin) -> None:
    """Agent CALLS API_ENDPOINT → AML.T0010."""
    agent = _node("my_agent", NodeType.AGENT)
    ep = _node("GET:/external", NodeType.API_ENDPOINT)
    doc = _doc(
        nodes=[agent, ep],
        edges=[Edge(source=agent.id, target=ep.id, relationship_type=EdgeRelationshipType.CALLS)],
    )
    result = plugin.run(doc)
    tech_ids = {ann["technique_id"] for ann in result.details}
    assert "AML.T0010" in tech_ids


def test_prompt_high_risk_maps_t0051(plugin: AtlasAnnotatorPlugin) -> None:
    """PROMPT with injection_risk_score > 0.7 → AML.T0051."""
    prompt = _node("sys_prompt", NodeType.PROMPT, extras={"injection_risk_score": 0.9})
    doc = _doc(nodes=[prompt], edges=[])
    result = plugin.run(doc)
    tech_ids = {ann["technique_id"] for ann in result.details}
    assert "AML.T0051" in tech_ids


def test_prompt_low_risk_no_t0051(plugin: AtlasAnnotatorPlugin) -> None:
    """PROMPT with injection_risk_score <= 0.7 → no AML.T0051."""
    prompt = _node("sys_prompt", NodeType.PROMPT, extras={"injection_risk_score": 0.3})
    doc = _doc(nodes=[prompt], edges=[])
    result = plugin.run(doc)
    tech_ids = {ann["technique_id"] for ann in result.details}
    assert "AML.T0051" not in tech_ids


def test_tool_no_auth_datastore_maps_t0025(plugin: AtlasAnnotatorPlugin) -> None:
    """TOOL without AUTH accessing DATASTORE → AML.T0025."""
    tool = _node("my_tool", NodeType.TOOL)
    ds = _node("my_db", NodeType.DATASTORE)
    doc = _doc(
        nodes=[tool, ds],
        edges=[Edge(source=tool.id, target=ds.id, relationship_type=EdgeRelationshipType.ACCESSES)],
    )
    result = plugin.run(doc)
    tech_ids = {ann["technique_id"] for ann in result.details}
    assert "AML.T0025" in tech_ids


def test_privilege_code_execution_maps_t0040(plugin: AtlasAnnotatorPlugin) -> None:
    """PRIVILEGE.code_execution → AML.T0040."""
    priv = _node("exec_priv", NodeType.PRIVILEGE, privilege_scope=PrivilegeScope.CODE_EXECUTION)
    doc = _doc(nodes=[priv], edges=[])
    result = plugin.run(doc)
    tech_ids = {ann["technique_id"] for ann in result.details}
    assert "AML.T0040" in tech_ids


def test_no_guardrail_maps_t0048(plugin: AtlasAnnotatorPlugin) -> None:
    """Agent with no GUARDRAIL → AML.T0048."""
    agent = _node("my_agent", NodeType.AGENT)
    doc = _doc(nodes=[agent], edges=[])
    result = plugin.run(doc)
    tech_ids = {ann["technique_id"] for ann in result.details}
    assert "AML.T0048" in tech_ids


def test_empty_sbom_no_annotations(plugin: AtlasAnnotatorPlugin) -> None:
    """Empty SBOM produces no annotations."""
    doc = _doc(nodes=[], edges=[])
    result = plugin.run(doc)
    assert result.details == []
    assert result.status == "pass"


def test_annotations_have_required_fields(plugin: AtlasAnnotatorPlugin) -> None:
    """Each annotation must have technique_id, technique_name, affected_nodes, confidence."""
    agent = _node("agent", NodeType.AGENT)
    ep = _node("ep", NodeType.API_ENDPOINT)
    doc = _doc(
        nodes=[agent, ep],
        edges=[Edge(source=agent.id, target=ep.id, relationship_type=EdgeRelationshipType.CALLS)],
    )
    result = plugin.run(doc)
    for ann in result.details:
        assert "technique_id" in ann
        assert "technique_name" in ann
        assert "affected_nodes" in ann
        assert "confidence" in ann
