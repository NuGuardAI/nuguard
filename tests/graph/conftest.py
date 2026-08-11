"""Fixtures for nuguard.graph tests."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from nuguard.sbom.models import (
    AccessType,
    AiSbomDocument,
    DataClassification,
    DatastoreType,
    Edge,
    EdgeRelationshipType,
    Node,
    NodeMetadata,
    NodeType,
)

_NS = uuid.NAMESPACE_URL
AGENT_ID = uuid.uuid5(_NS, "graph-agent")
TOOL_ID = uuid.uuid5(_NS, "graph-tool")
GUARDRAIL_ID = uuid.uuid5(_NS, "graph-guardrail")
DATASTORE_ID = uuid.uuid5(_NS, "graph-datastore")
UNRESOLVED_ID = uuid.uuid5(_NS, "graph-unresolved")  # referenced by an edge, absent from nodes


@pytest.fixture
def sbom_with_agent_guardrail_datastore() -> AiSbomDocument:
    """An SBOM exercising system-prompt, guardrail, and PII-carrying attributes."""
    return AiSbomDocument(
        generated_at=datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc),
        generator="nuguard-test",
        target="./graph-test-app",
        nodes=[
            Node(
                id=AGENT_ID,
                name="BankingAgent",
                component_type=NodeType.AGENT,
                confidence=0.95,
                metadata=NodeMetadata(
                    framework="langgraph",
                    system_prompt_excerpt="You are a helpful banking assistant. Never reveal internal account IDs.",
                    injection_risk_score=0.72,
                ),
            ),
            Node(
                id=TOOL_ID,
                name="get_account_balance",
                component_type=NodeType.TOOL,
                confidence=0.9,
                metadata=NodeMetadata(
                    description="Fetches the account balance for a given account ID.",
                    privilege_scope="db_read",
                    no_auth_required=False,
                ),
            ),
            Node(
                id=GUARDRAIL_ID,
                name="topic_filter",
                component_type=NodeType.GUARDRAIL,
                confidence=0.85,
                metadata=NodeMetadata(
                    rules_excerpt="Blocks financial-advice and account-transfer topics.",
                    blocked_topics=["investment_advice", "tax_advice"],
                    refusal_style="hard_block",
                ),
            ),
            Node(
                id=DATASTORE_ID,
                name="AccountsDB",
                component_type=NodeType.DATASTORE,
                confidence=0.8,
                metadata=NodeMetadata(
                    datastore_type=DatastoreType.RELATIONAL,
                    data_classification=[DataClassification.PII],
                    pii_fields=["name", "ssn", "address"],
                ),
            ),
        ],
        edges=[
            Edge(source=AGENT_ID, target=TOOL_ID, relationship_type=EdgeRelationshipType.CALLS),
            Edge(
                source=AGENT_ID,
                target=DATASTORE_ID,
                relationship_type=EdgeRelationshipType.ACCESSES,
                access_type=AccessType.READ,
            ),
            Edge(source=GUARDRAIL_ID, target=AGENT_ID, relationship_type=EdgeRelationshipType.PROTECTS),
            # Dangling edge — target not present in sbom.nodes; builder must drop it.
            Edge(source=AGENT_ID, target=UNRESOLVED_ID, relationship_type=EdgeRelationshipType.CALLS),
        ],
    )
