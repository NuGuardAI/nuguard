"""Tests for SBOM-grounded datastore attack scenarios.

Covers build_datastore_schema_probe, build_datastore_sql_injection,
build_cross_account_tool_abuse, and their integration with ScenarioGenerator.
"""
from __future__ import annotations

import uuid

import pytest

from nuguard.models.exploit_chain import GoalType, ScenarioType
from nuguard.redteam.scenarios.data_exfiltration import (
    build_cross_account_tool_abuse,
    build_datastore_schema_probe,
    build_datastore_sql_injection,
)
from nuguard.redteam.scenarios.generator import ScenarioGenerator
from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata
from nuguard.sbom.types import ComponentType, RelationshipType

_NS = uuid.NAMESPACE_URL


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


def _make_fintech_like_sbom() -> AiSbomDocument:
    """1 AGENT → CALLS → [bulk_export_all_customers, list_all_accounts]
    1 DATASTORE (sqlite, classified_fields={"LoginRequest": ["password","user_id"]})
    """
    agent = Node(
        id=uuid.uuid5(_NS, "fintech-agent"),
        name="FintechAgent",
        component_type=ComponentType.AGENT,
        confidence=0.9,
        metadata=NodeMetadata(),
    )
    tool_bulk = Node(
        id=uuid.uuid5(_NS, "bulk_export_all_customers"),
        name="bulk_export_all_customers",
        component_type=ComponentType.TOOL,
        confidence=0.9,
        metadata=NodeMetadata(no_auth_required=True, high_privilege=True),
    )
    tool_list = Node(
        id=uuid.uuid5(_NS, "list_all_accounts"),
        name="list_all_accounts",
        component_type=ComponentType.TOOL,
        confidence=0.9,
        metadata=NodeMetadata(no_auth_required=True, high_privilege=True),
    )
    datastore = Node(
        id=uuid.uuid5(_NS, "fintech-sqlite"),
        name="sqlite",
        component_type=ComponentType.DATASTORE,
        confidence=0.9,
        metadata=NodeMetadata(
            datastore_type="sqlite",
            classified_fields={"LoginRequest": ["password", "user_id"]},
            pii_fields=["user_id", "email"],
        ),
    )
    return AiSbomDocument(
        target="fintech-test",
        nodes=[agent, tool_bulk, tool_list, datastore],
        edges=[
            Edge(
                source=agent.id,
                target=tool_bulk.id,
                relationship_type=RelationshipType.CALLS,
            ),
            Edge(
                source=agent.id,
                target=tool_list.id,
                relationship_type=RelationshipType.CALLS,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# build_datastore_schema_probe — unit tests
# ---------------------------------------------------------------------------


def test_schema_probe_goal_and_scenario_type():
    s = build_datastore_schema_probe(
        agent_id="a", agent_name="Agent",
        datastore_name="sqlite", table_names=["LoginRequest"],
        primary_key_fields=["user_id"],
    )
    assert s.goal_type == GoalType.DATA_EXFILTRATION
    assert s.scenario_type == ScenarioType.DATASTORE_PROBE


def test_schema_probe_owasp_refs():
    s = build_datastore_schema_probe(
        agent_id="a", agent_name="Agent",
        datastore_name="db", table_names=["users"],
        primary_key_fields=[],
    )
    assert s.chain.owasp_asi_ref == "ASI05"
    assert s.chain.owasp_llm_ref == "LLM06"


def test_schema_probe_has_four_steps():
    s = build_datastore_schema_probe(
        agent_id="a", agent_name="Agent",
        datastore_name="db", table_names=["users"],
        primary_key_fields=[],
    )
    assert len(s.chain.steps) == 4


def test_schema_probe_step3_injects_real_table_name():
    s = build_datastore_schema_probe(
        agent_id="a", agent_name="Agent",
        datastore_name="sqlite",
        table_names=["AirlineAgentContext", "GuardrailCheck"],
        primary_key_fields=["account_number"],
    )
    assert "AirlineAgentContext" in s.chain.steps[2].payload


def test_schema_probe_step4_references_pk_field_when_provided():
    s = build_datastore_schema_probe(
        agent_id="a", agent_name="Agent",
        datastore_name="redis", table_names=["LoginRequest"],
        primary_key_fields=["account_id"],
    )
    assert "account_id" in s.chain.steps[3].payload


def test_schema_probe_empty_pk_fields_does_not_crash():
    s = build_datastore_schema_probe(
        agent_id="a", agent_name="Agent",
        datastore_name="sqlite", table_names=["users"],
        primary_key_fields=[],
    )
    assert s.chain.steps[3].payload  # non-empty


def test_schema_probe_step1_no_llm_eval_steps234_require_llm_eval():
    s = build_datastore_schema_probe(
        agent_id="a", agent_name="Agent",
        datastore_name="db", table_names=["orders"],
        primary_key_fields=[],
    )
    steps = s.chain.steps
    assert steps[0].use_llm_eval is False
    assert steps[1].use_llm_eval is True
    assert steps[2].use_llm_eval is True
    assert steps[3].use_llm_eval is True


def test_schema_probe_impact_score_at_least_8_5():
    s = build_datastore_schema_probe("a", "Agent", "db", ["t"], [])
    assert s.impact_score >= 8.5


# ---------------------------------------------------------------------------
# build_datastore_sql_injection — unit tests
# ---------------------------------------------------------------------------


def test_sql_injection_goal_and_scenario_type():
    s = build_datastore_sql_injection(
        agent_id="a", agent_name="Agent",
        datastore_name="sqlite", datastore_type="sqlite",
        pii_fields=["email"],
    )
    assert s.goal_type == GoalType.DATA_EXFILTRATION
    assert s.scenario_type == ScenarioType.DATASTORE_SQL_INJECTION


def test_sql_injection_owasp_refs():
    s = build_datastore_sql_injection(
        agent_id="a", agent_name="Agent",
        datastore_name="db", datastore_type="sqlite",
        pii_fields=[],
    )
    assert s.chain.owasp_asi_ref == "ASI05"
    assert s.chain.owasp_llm_ref == "LLM04"


def test_sql_injection_has_four_steps():
    s = build_datastore_sql_injection(
        agent_id="a", agent_name="Agent",
        datastore_name="db", datastore_type="sqlite",
        pii_fields=["email"],
    )
    assert len(s.chain.steps) == 4


def test_sql_injection_step2_uses_pii_field_and_or_clause():
    s = build_datastore_sql_injection(
        agent_id="a", agent_name="Agent",
        datastore_name="db", datastore_type="sqlite",
        pii_fields=["email", "phone"],
    )
    payload = s.chain.steps[1].payload
    assert "email" in payload
    assert "OR" in payload.upper()


def test_sql_injection_step3_contains_union_and_field_names():
    s = build_datastore_sql_injection(
        agent_id="a", agent_name="Agent",
        datastore_name="db", datastore_type="postgres",
        pii_fields=["account_number", "name"],
    )
    payload = s.chain.steps[2].payload.upper()
    assert "UNION" in payload
    assert "account_number" in s.chain.steps[2].payload or "name" in s.chain.steps[2].payload


def test_sql_injection_sqlite_blind_payload_uses_case_not_sleep():
    s = build_datastore_sql_injection(
        agent_id="a", agent_name="Agent",
        datastore_name="db", datastore_type="sqlite",
        pii_fields=[],
    )
    payload = s.chain.steps[3].payload.upper()
    assert "SLEEP" not in payload or "CASE" in payload or "LOAD_EXTENSION" in payload.lower()


def test_sql_injection_postgres_blind_payload_uses_pg_sleep():
    s = build_datastore_sql_injection(
        agent_id="a", agent_name="Agent",
        datastore_name="db", datastore_type="postgres",
        pii_fields=[],
    )
    assert "pg_sleep" in s.chain.steps[3].payload


def test_sql_injection_empty_pii_fields_fallback_payload_non_empty():
    s = build_datastore_sql_injection("a", "Agent", "db", "sqlite", pii_fields=[])
    assert len(s.chain.steps) == 4
    for step in s.chain.steps:
        assert step.payload  # no empty payloads


def test_sql_injection_impact_score_at_least_10():
    s = build_datastore_sql_injection("a", "Agent", "db", "sqlite", pii_fields=["email"])
    assert s.impact_score >= 10.0


# ---------------------------------------------------------------------------
# build_cross_account_tool_abuse — unit tests
# ---------------------------------------------------------------------------


def test_cross_account_tool_abuse_goal_and_scenario_type():
    s = build_cross_account_tool_abuse(
        agent_id="a", agent_name="FintechAgent",
        tool_names=["list_all_accounts", "bulk_export_all_customers"],
        datastore_name="sqlite", pii_fields=["account_id", "email"],
    )
    assert s.goal_type == GoalType.DATA_EXFILTRATION
    assert s.scenario_type == ScenarioType.BULK_DATA_EXPORT


def test_cross_account_tool_abuse_owasp_refs():
    s = build_cross_account_tool_abuse(
        agent_id="a", agent_name="Agent",
        tool_names=["export_all_audit_logs"],
        datastore_name="db", pii_fields=[],
    )
    assert s.chain.owasp_asi_ref == "ASI03"
    assert s.chain.owasp_llm_ref == "LLM06"


def test_cross_account_tool_abuse_has_three_steps():
    s = build_cross_account_tool_abuse(
        agent_id="a", agent_name="Agent",
        tool_names=["list_all_accounts"],
        datastore_name="db", pii_fields=[],
    )
    assert len(s.chain.steps) == 3


def test_cross_account_tool_abuse_step1_references_first_tool():
    s = build_cross_account_tool_abuse(
        agent_id="a", agent_name="Agent",
        tool_names=["export_all_audit_logs", "list_all_users"],
        datastore_name="redis", pii_fields=[],
    )
    assert "export_all_audit_logs" in s.chain.steps[0].payload


def test_cross_account_tool_abuse_step2_uses_audit_pretext():
    s = build_cross_account_tool_abuse(
        agent_id="a", agent_name="Agent",
        tool_names=["list_all_accounts"],
        datastore_name="db", pii_fields=["account_id"],
    )
    payload_lower = s.chain.steps[1].payload.lower()
    assert "audit" in payload_lower or "compliance" in payload_lower or "all accounts" in payload_lower


def test_cross_account_tool_abuse_step3_names_datastore():
    s = build_cross_account_tool_abuse(
        agent_id="a", agent_name="Agent",
        tool_names=["bulk_export"],
        datastore_name="customer_db", pii_fields=[],
    )
    assert "customer_db" in s.chain.steps[2].payload


def test_cross_account_tool_abuse_steps2_and_3_require_llm_eval():
    s = build_cross_account_tool_abuse("a", "Agent", ["list_all"], "db", [])
    assert s.chain.steps[1].use_llm_eval is True
    assert s.chain.steps[2].use_llm_eval is True


def test_cross_account_tool_abuse_impact_score_at_least_10():
    s = build_cross_account_tool_abuse("a", "Agent", ["list_all"], "db", ["email"])
    assert s.impact_score >= 10.0


def test_cross_account_tool_abuse_empty_tool_names_does_not_crash():
    s = build_cross_account_tool_abuse("a", "Agent", [], "db", [])
    assert len(s.chain.steps) == 3
    assert s.chain.steps[0].payload


# ---------------------------------------------------------------------------
# ScenarioGenerator integration tests
# ---------------------------------------------------------------------------


def test_generator_produces_datastore_probe_for_classified_datastore():
    sbom = _make_fintech_like_sbom()
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    types = {s.scenario_type for s in scenarios}
    assert ScenarioType.DATASTORE_PROBE in types


def test_generator_produces_sql_injection_for_sqlite_datastore():
    sbom = _make_fintech_like_sbom()
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    types = {s.scenario_type for s in scenarios}
    assert ScenarioType.DATASTORE_SQL_INJECTION in types


def test_generator_produces_bulk_export_for_privileged_tools():
    sbom = _make_fintech_like_sbom()
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    types = {s.scenario_type for s in scenarios}
    assert ScenarioType.BULK_DATA_EXPORT in types


def test_generator_no_datastore_probe_without_classified_fields():
    agent = Node(id=uuid.uuid4(), name="Agent", component_type=ComponentType.AGENT,
                 confidence=0.9, metadata=NodeMetadata())
    ds = Node(id=uuid.uuid4(), name="redis", component_type=ComponentType.DATASTORE,
              confidence=0.9, metadata=NodeMetadata(datastore_type="redis"))
    sbom = AiSbomDocument(target="t", nodes=[agent, ds], edges=[])
    types = {s.scenario_type for s in ScenarioGenerator(sbom).generate()}
    assert ScenarioType.DATASTORE_PROBE not in types


def test_generator_no_sql_injection_for_non_sql_datastore():
    agent = Node(id=uuid.uuid4(), name="Agent", component_type=ComponentType.AGENT,
                 confidence=0.9, metadata=NodeMetadata())
    ds = Node(id=uuid.uuid4(), name="pinecone", component_type=ComponentType.DATASTORE,
              confidence=0.9, metadata=NodeMetadata(
                  datastore_type="vector",
                  classified_fields={"vectors": ["embedding"]},
                  pii_fields=["user_id"],
              ))
    sbom = AiSbomDocument(target="t", nodes=[agent, ds], edges=[])
    types = {s.scenario_type for s in ScenarioGenerator(sbom).generate()}
    assert ScenarioType.DATASTORE_SQL_INJECTION not in types


def test_generator_no_bulk_export_without_privileged_tools():
    agent = Node(id=uuid.uuid4(), name="Agent", component_type=ComponentType.AGENT,
                 confidence=0.9, metadata=NodeMetadata())
    tool = Node(id=uuid.uuid4(), name="get_weather", component_type=ComponentType.TOOL,
                confidence=0.9, metadata=NodeMetadata())
    sbom = AiSbomDocument(
        target="t", nodes=[agent, tool],
        edges=[Edge(source=agent.id, target=tool.id, relationship_type=RelationshipType.CALLS)],
    )
    types = {s.scenario_type for s in ScenarioGenerator(sbom).generate()}
    assert ScenarioType.BULK_DATA_EXPORT not in types


def test_agents_with_data_tools_returns_agents_with_lookup_tools():
    agent = Node(id=uuid.uuid4(), name="Agent", component_type=ComponentType.AGENT,
                 confidence=0.9, metadata=NodeMetadata())
    lookup_tool = Node(id=uuid.uuid4(), name="booking_lookup_tool",
                       component_type=ComponentType.TOOL, confidence=0.9,
                       metadata=NodeMetadata())
    sbom = AiSbomDocument(
        target="t", nodes=[agent, lookup_tool],
        edges=[Edge(source=agent.id, target=lookup_tool.id,
                    relationship_type=RelationshipType.CALLS)],
    )
    result = ScenarioGenerator(sbom)._agents_with_data_tools()
    assert str(agent.id) in result


def test_schema_probe_payloads_contain_actual_table_names():
    sbom = _make_fintech_like_sbom()
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    probe_scenarios = [s for s in scenarios if s.scenario_type == ScenarioType.DATASTORE_PROBE]
    assert probe_scenarios, "Expected at least one DATASTORE_PROBE scenario"
    step3_payload = probe_scenarios[0].chain.steps[2].payload
    assert "LoginRequest" in step3_payload
