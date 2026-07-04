"""Tests for expanded static alignment checks BA-009 through BA-016.

Each test provides a minimal SBOM fixture that triggers exactly one check
and verifies the expected finding is produced (or not produced).
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from nuguard.behavior.alignment import check_alignment
from nuguard.behavior.models import IntentProfile
from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata
from nuguard.sbom.types import AccessType, ComponentType, RelationshipType

_NS = uuid.NAMESPACE_URL


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _node(name: str, ctype: ComponentType, **meta_kwargs: object) -> Node:
    nid = uuid.uuid5(_NS, f"{ctype.value}/{name}")
    return Node(
        id=nid,
        name=name,
        component_type=ctype,
        confidence=1.0,
        metadata=NodeMetadata(**meta_kwargs),
    )


def _edge(src: Node, tgt: Node, rel: RelationshipType, access_type: AccessType | None = None) -> Edge:
    return Edge(source=src.id, target=tgt.id, relationship_type=rel, access_type=access_type)


def _policy(**kwargs: object) -> MagicMock:
    policy = MagicMock()
    policy.restricted_topics = kwargs.get("restricted_topics", [])
    policy.allowed_topics = kwargs.get("allowed_topics", [])
    policy.restricted_actions = kwargs.get("restricted_actions", [])
    policy.hitl_triggers = kwargs.get("hitl_triggers", [])
    return policy


def _intent() -> IntentProfile:
    return IntentProfile(app_purpose="test application")


def _sbom(*nodes: Node, edges: list[Edge] | None = None) -> AiSbomDocument:
    return AiSbomDocument(target="./app", nodes=list(nodes), edges=edges or [])


def _finding_ids(findings: list) -> list[str]:
    return [str(f.get("finding_id") if isinstance(f, dict) else f.finding_id) for f in findings]


def _check(sbom: AiSbomDocument, **policy_kwargs: object) -> list:
    from nuguard.models.finding import Finding
    policy = _policy(**policy_kwargs)
    intent = _intent()
    results = check_alignment(sbom, intent, policy)
    return results


# ---------------------------------------------------------------------------
# BA-009: Unprotected sensitive endpoints / agents
# ---------------------------------------------------------------------------


class TestBA009:
    def test_fires_for_sensitive_endpoint_without_auth(self) -> None:
        ep = _node("/api/records", ComponentType.API_ENDPOINT,
                   auth_required=False, returns_sensitive_data=True)
        sbom = _sbom(ep)
        findings = _check(sbom)
        ba9 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-009")]
        assert ba9, "Expected BA-009 finding for unprotected sensitive endpoint"

    def test_no_finding_when_auth_node_protects(self) -> None:
        ep = _node("/api/records", ComponentType.API_ENDPOINT,
                   auth_required=False, returns_sensitive_data=True)
        auth = _node("AuthLayer", ComponentType.AUTH)
        sbom = _sbom(ep, auth, edges=[_edge(auth, ep, RelationshipType.PROTECTS)])
        findings = _check(sbom)
        ba9 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-009")]
        assert not ba9

    def test_no_finding_when_auth_required_on_node(self) -> None:
        ep = _node("/api/records", ComponentType.API_ENDPOINT,
                   auth_required=True, returns_sensitive_data=True)
        sbom = _sbom(ep)
        findings = _check(sbom)
        ba9 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-009")]
        assert not ba9

    def test_no_finding_for_non_sensitive_endpoint(self) -> None:
        ep = _node("/api/health", ComponentType.API_ENDPOINT,
                   auth_required=False, returns_sensitive_data=False)
        sbom = _sbom(ep)
        findings = _check(sbom)
        ba9 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-009")]
        assert not ba9


# ---------------------------------------------------------------------------
# BA-010: Unguarded high-privilege component
# ---------------------------------------------------------------------------


class TestBA010:
    def test_fires_for_unprotected_privilege_node(self) -> None:
        priv = _node("AdminPrivilege", ComponentType.PRIVILEGE)
        sbom = _sbom(priv)
        findings = _check(sbom)
        ba10 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-010")]
        assert ba10

    def test_fires_for_unprotected_high_privilege_tool(self) -> None:
        tool = _node("AdminTool", ComponentType.TOOL, high_privilege=True)
        sbom = _sbom(tool)
        findings = _check(sbom)
        ba10 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-010")]
        assert ba10

    def test_no_finding_when_guardrail_protects(self) -> None:
        tool = _node("AdminTool", ComponentType.TOOL, high_privilege=True)
        guardrail = _node("Guard", ComponentType.GUARDRAIL)
        sbom = _sbom(tool, guardrail, edges=[_edge(guardrail, tool, RelationshipType.PROTECTS)])
        findings = _check(sbom)
        ba10 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-010")]
        assert not ba10

    def test_no_finding_for_normal_tool(self) -> None:
        tool = _node("NormalTool", ComponentType.TOOL, high_privilege=False)
        sbom = _sbom(tool)
        findings = _check(sbom)
        ba10 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-010")]
        assert not ba10


# ---------------------------------------------------------------------------
# BA-011: Write access to datastore without control
# ---------------------------------------------------------------------------


class TestBA011:
    def test_fires_for_write_accesses_without_guardrail(self) -> None:
        tool = _node("WriteTool", ComponentType.TOOL)
        ds = _node("DB", ComponentType.DATASTORE)
        e = _edge(tool, ds, RelationshipType.ACCESSES, AccessType.WRITE)
        sbom = _sbom(tool, ds, edges=[e])
        findings = _check(sbom)
        ba11 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-011")]
        assert ba11

    def test_fires_for_readwrite_accesses(self) -> None:
        tool = _node("RWTool", ComponentType.TOOL)
        ds = _node("DB", ComponentType.DATASTORE)
        e = _edge(tool, ds, RelationshipType.ACCESSES, AccessType.READWRITE)
        sbom = _sbom(tool, ds, edges=[e])
        findings = _check(sbom)
        ba11 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-011")]
        assert ba11

    def test_no_finding_for_read_only_accesses(self) -> None:
        tool = _node("ReadTool", ComponentType.TOOL)
        ds = _node("DB", ComponentType.DATASTORE)
        e = _edge(tool, ds, RelationshipType.ACCESSES, AccessType.READ)
        sbom = _sbom(tool, ds, edges=[e])
        findings = _check(sbom)
        ba11 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-011")]
        assert not ba11

    def test_no_finding_when_guardrail_protects_datastore(self) -> None:
        tool = _node("WriteTool", ComponentType.TOOL)
        ds = _node("DB", ComponentType.DATASTORE)
        guard = _node("Guard", ComponentType.GUARDRAIL)
        sbom = _sbom(tool, ds, guard, edges=[
            _edge(tool, ds, RelationshipType.ACCESSES, AccessType.WRITE),
            _edge(guard, ds, RelationshipType.PROTECTS),
        ])
        findings = _check(sbom)
        ba11 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-011")]
        assert not ba11


# ---------------------------------------------------------------------------
# BA-012: External MODEL with sensitive data access
# ---------------------------------------------------------------------------


class TestBA012:
    def test_fires_when_agent_uses_external_model_and_reaches_pii(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        model = _node("GPT4", ComponentType.MODEL, source_url="https://api.openai.com")
        tool = _node("DBTool", ComponentType.TOOL)
        ds = _node("DB", ComponentType.DATASTORE, pii_fields=["email", "name"])
        sbom = _sbom(agent, model, tool, ds, edges=[
            _edge(agent, model, RelationshipType.USES),
            _edge(agent, tool, RelationshipType.CALLS),
            _edge(tool, ds, RelationshipType.ACCESSES, AccessType.READ),
        ])
        findings = _check(sbom)
        ba12 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-012")]
        assert ba12

    def test_no_finding_when_model_is_local(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        model = _node("LocalModel", ComponentType.MODEL, source_url="./models/llama.gguf")
        tool = _node("DBTool", ComponentType.TOOL)
        ds = _node("DB", ComponentType.DATASTORE, pii_fields=["email"])
        sbom = _sbom(agent, model, tool, ds, edges=[
            _edge(agent, model, RelationshipType.USES),
            _edge(agent, tool, RelationshipType.CALLS),
            _edge(tool, ds, RelationshipType.ACCESSES, AccessType.READ),
        ])
        findings = _check(sbom)
        ba12 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-012")]
        assert not ba12

    def test_no_finding_when_no_sensitive_datastore(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        model = _node("GPT4", ComponentType.MODEL, source_url="https://api.openai.com")
        sbom = _sbom(agent, model, edges=[_edge(agent, model, RelationshipType.USES)])
        findings = _check(sbom)
        ba12 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-012")]
        assert not ba12


# ---------------------------------------------------------------------------
# BA-013: PROMPT contains restricted topic
# ---------------------------------------------------------------------------


class TestBA013:
    def test_fires_when_prompt_mentions_restricted_topic(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        prompt = _node("SysPrompt", ComponentType.PROMPT,
                       system_prompt_excerpt="You can help with financial advice and investment planning.")
        sbom = _sbom(agent, prompt, edges=[_edge(agent, prompt, RelationshipType.USES)])
        findings = _check(sbom, restricted_topics=["financial advice"])
        ba13 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-013")]
        assert ba13

    def test_no_finding_when_prompt_clean(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        prompt = _node("SysPrompt", ComponentType.PROMPT,
                       system_prompt_excerpt="You are a helpful customer support agent.")
        sbom = _sbom(agent, prompt, edges=[_edge(agent, prompt, RelationshipType.USES)])
        findings = _check(sbom, restricted_topics=["financial advice"])
        ba13 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-013")]
        assert not ba13

    def test_no_finding_when_no_restricted_topics(self) -> None:
        agent = _node("Agent", ComponentType.AGENT)
        prompt = _node("SysPrompt", ComponentType.PROMPT,
                       system_prompt_excerpt="You can help with financial advice.")
        sbom = _sbom(agent, prompt, edges=[_edge(agent, prompt, RelationshipType.USES)])
        findings = _check(sbom, restricted_topics=[])
        ba13 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-013")]
        assert not ba13


# ---------------------------------------------------------------------------
# BA-014: Unsafe DELEGATES_TO (privilege escalation)
# ---------------------------------------------------------------------------


class TestBA014:
    def test_fires_when_delegation_escalates_privilege(self) -> None:
        src = _node("NormalAgent", ComponentType.AGENT, high_privilege=False)
        tgt = _node("AdminAgent", ComponentType.AGENT, high_privilege=True)
        sbom = _sbom(src, tgt, edges=[_edge(src, tgt, RelationshipType.DELEGATES_TO)])
        findings = _check(sbom)
        ba14 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-014")]
        assert ba14

    def test_no_finding_when_guardrail_protects_downstream(self) -> None:
        src = _node("NormalAgent", ComponentType.AGENT, high_privilege=False)
        tgt = _node("AdminAgent", ComponentType.AGENT, high_privilege=True)
        guard = _node("Guard", ComponentType.GUARDRAIL)
        sbom = _sbom(src, tgt, guard, edges=[
            _edge(src, tgt, RelationshipType.DELEGATES_TO),
            _edge(guard, tgt, RelationshipType.PROTECTS),
        ])
        findings = _check(sbom)
        ba14 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-014")]
        assert not ba14

    def test_no_finding_when_equal_privilege(self) -> None:
        src = _node("Agent1", ComponentType.AGENT, high_privilege=False)
        tgt = _node("Agent2", ComponentType.AGENT, high_privilege=False)
        sbom = _sbom(src, tgt, edges=[_edge(src, tgt, RelationshipType.DELEGATES_TO)])
        findings = _check(sbom)
        ba14 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-014")]
        assert not ba14


# ---------------------------------------------------------------------------
# BA-015: Deployment posture issues
# ---------------------------------------------------------------------------


class TestBA015:
    def test_fires_when_container_runs_as_root(self) -> None:
        agent = _node("App", ComponentType.AGENT)
        deploy = _node("k8s-deploy", ComponentType.DEPLOYMENT, runs_as_root=True)
        sbom = _sbom(agent, deploy, edges=[_edge(agent, deploy, RelationshipType.DEPLOYS)])
        findings = _check(sbom)
        ba15 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-015")]
        assert ba15

    def test_fires_when_no_health_check(self) -> None:
        agent = _node("App", ComponentType.AGENT)
        deploy = _node("k8s-deploy", ComponentType.DEPLOYMENT, has_health_check=False)
        sbom = _sbom(agent, deploy, edges=[_edge(agent, deploy, RelationshipType.DEPLOYS)])
        findings = _check(sbom)
        ba15 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-015")]
        assert ba15

    def test_no_finding_when_posture_is_clean(self) -> None:
        agent = _node("App", ComponentType.AGENT)
        deploy = _node("k8s-deploy", ComponentType.DEPLOYMENT,
                       runs_as_root=False, has_health_check=True,
                       has_resource_limits=True, has_network_policy=True)
        sbom = _sbom(agent, deploy, edges=[_edge(agent, deploy, RelationshipType.DEPLOYS)])
        findings = _check(sbom)
        ba15 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-015")]
        assert not ba15

    def test_no_finding_without_deploys_edge(self) -> None:
        deploy = _node("k8s-deploy", ComponentType.DEPLOYMENT, runs_as_root=True)
        sbom = _sbom(deploy)
        findings = _check(sbom)
        ba15 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-015")]
        assert not ba15


# ---------------------------------------------------------------------------
# BA-016: Sensitive endpoint without auth or guardrail
# ---------------------------------------------------------------------------


class TestBA016:
    def test_fires_for_sensitive_endpoint_without_any_protection(self) -> None:
        ep = _node("/api/records", ComponentType.API_ENDPOINT,
                   returns_sensitive_data=True, auth_required=False)
        sbom = _sbom(ep)
        findings = _check(sbom)
        ba16 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-016")]
        assert ba16

    def test_no_finding_when_auth_required(self) -> None:
        ep = _node("/api/records", ComponentType.API_ENDPOINT,
                   returns_sensitive_data=True, auth_required=True)
        sbom = _sbom(ep)
        findings = _check(sbom)
        ba16 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-016")]
        assert not ba16

    def test_no_finding_when_guardrail_protects(self) -> None:
        ep = _node("/api/records", ComponentType.API_ENDPOINT,
                   returns_sensitive_data=True, auth_required=False)
        guard = _node("Guard", ComponentType.GUARDRAIL)
        sbom = _sbom(ep, guard, edges=[_edge(guard, ep, RelationshipType.PROTECTS)])
        findings = _check(sbom)
        ba16 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-016")]
        assert not ba16

    def test_no_finding_for_non_sensitive_endpoint(self) -> None:
        ep = _node("/api/health", ComponentType.API_ENDPOINT,
                   returns_sensitive_data=False)
        sbom = _sbom(ep)
        findings = _check(sbom)
        ba16 = [f for f in findings if str(getattr(f, "finding_id", "")).startswith("BA-016")]
        assert not ba16
