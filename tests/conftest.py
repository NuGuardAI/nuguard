"""Shared pytest fixtures for nuguard tests."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nuguard.sbom.models import (
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

_ENV_FILE = Path(__file__).parent / "redteam" / ".env"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader — no external dependency required."""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# Load tests/.env before any test collection so all tests can see the keys
if _ENV_FILE.exists():
    _load_dotenv(_ENV_FILE)

_NS = uuid.NAMESPACE_URL
_AGENT_ID = uuid.uuid5(_NS, "agent001")
_TOOL_ID = uuid.uuid5(_NS, "tool001")
_DS_ID = uuid.uuid5(_NS, "ds001")


@pytest.fixture
def minimal_sbom_doc() -> AiSbomDocument:
    """A minimal but valid AiSbomDocument for use across test modules."""
    return AiSbomDocument(
        generated_at=datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc),
        generator="nuguard-test",
        target="./test-agent-app",
        nodes=[
            Node(
                id=_AGENT_ID,
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
                id=_TOOL_ID,
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
                id=_DS_ID,
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
                source=_AGENT_ID,
                target=_TOOL_ID,
                relationship_type=EdgeRelationshipType.CALLS,
            ),
            Edge(
                source=_AGENT_ID,
                target=_DS_ID,
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
