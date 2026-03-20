"""Shared pytest fixtures for nuguard tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nuguard.models.sbom import (
    AccessType,
    AiSbomDocument,
    DataClassification,
    DatastoreType,
    Edge,
    EdgeRelationshipType,
    Evidence,
    EvidenceKind,
    EvidenceLocation,
    Node,
    NodeMetadata,
    NodeType,
    ScanSummary,
)


@pytest.fixture
def minimal_sbom_doc() -> AiSbomDocument:
    """A minimal but valid AiSbomDocument for use across test modules."""
    return AiSbomDocument(
        schema_version="1.3.0",
        generated_at=datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc),
        generator="nuguard-test",
        target="./test-agent-app",
        nodes=[
            Node(
                id="agent001",
                name="CustomerSupportAgent",
                component_type=NodeType.AGENT,
                confidence=0.95,
                metadata=NodeMetadata(framework="langchain"),
                evidence=[
                    Evidence(
                        kind=EvidenceKind.AST,
                        confidence=0.95,
                        detail="AgentExecutor detected",
                        location=EvidenceLocation(path="app/agent.py", line=42),
                    )
                ],
            ),
            Node(
                id="tool001",
                name="search_knowledge_base",
                component_type=NodeType.TOOL,
                confidence=0.9,
                metadata=NodeMetadata(framework="langchain"),
                evidence=[
                    Evidence(
                        kind=EvidenceKind.AST,
                        confidence=0.9,
                        detail="@tool decorated function",
                        location=EvidenceLocation(path="app/tools.py", line=15),
                    )
                ],
            ),
            Node(
                id="ds001",
                name="CustomerDatabase",
                component_type=NodeType.DATASTORE,
                confidence=0.8,
                metadata=NodeMetadata(
                    datastore_type=DatastoreType.RELATIONAL,
                    data_classification=[DataClassification.PII],
                    classified_tables=["customers", "orders"],
                ),
            ),
        ],
        edges=[
            Edge(
                source="agent001",
                target="tool001",
                relationship_type=EdgeRelationshipType.CALLS,
            ),
            Edge(
                source="agent001",
                target="ds001",
                relationship_type=EdgeRelationshipType.ACCESSES,
                access_type=AccessType.READWRITE,
            ),
        ],
        summary=ScanSummary(
            frameworks=["langchain"],
            node_counts={"AGENT": 1, "TOOL": 1, "DATASTORE": 1},
            data_classification=["PII"],
            classified_tables=["customers", "orders"],
        ),
    )


@pytest.fixture
def minimal_sbom_dict(minimal_sbom_doc: AiSbomDocument) -> dict:
    """The minimal SBOM document serialized to a plain dict."""
    return minimal_sbom_doc.model_dump(mode="json")
