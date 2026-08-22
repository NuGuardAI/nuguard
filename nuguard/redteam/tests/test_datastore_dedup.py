"""Tests for datastore-alias dedup in ScenarioGenerator._exfiltration_scenarios.

SBOM extraction commonly detects the same physical datastore multiple times
under different driver/ORM aliases (e.g. "Sqlite"/"Sqlite3"/"Sqlalchemy" all
pointing at one relational DB). Without dedup, one schema-probe + one
SQL-injection scenario is generated per alias, burning most of the scenario
budget on near-duplicate turns against the same underlying attack surface.
"""
from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime

from nuguard.redteam.scenarios.generator import ScenarioGenerator
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata, ScanSummary
from nuguard.sbom.types import ComponentType


def _make_sbom(nodes: list[Node]) -> AiSbomDocument:
    return AiSbomDocument(
        generated_at=datetime.now(UTC),
        target="test-app",
        nodes=nodes,
        edges=[],
        summary=ScanSummary(),
    )


def _agent_node(name: str = "Agent") -> Node:
    return Node(
        id=_uuid.uuid5(_uuid.NAMESPACE_URL, f"agent/{name}"),
        name=name,
        component_type=ComponentType.AGENT,
        confidence=0.9,
        metadata=NodeMetadata(),
    )


def _datastore_node(name: str, datastore_type: str, confidence: float) -> Node:
    return Node(
        id=_uuid.uuid5(_uuid.NAMESPACE_URL, f"datastore/{name}"),
        name=name,
        component_type=ComponentType.DATASTORE,
        confidence=confidence,
        metadata=NodeMetadata(
            datastore_type=datastore_type,
            pii_fields=["email"],
            classified_fields={"users": ["email"]},
        ),
    )


def test_relational_aliases_collapse_to_one_datastore() -> None:
    """Sqlite/Sqlite3/Sqlalchemy/Postgres/Postgresql all detected as 'relational'
    should produce scenarios for exactly one of them — the highest-confidence node."""
    nodes = [
        _agent_node(),
        _datastore_node("Sqlite", "relational", 0.6),
        _datastore_node("Sqlite3", "relational", 0.7),
        _datastore_node("Sqlalchemy", "relational", 0.9),  # highest confidence — should win
        _datastore_node("Postgres", "relational", 0.5),
        _datastore_node("Postgresql", "relational", 0.65),
    ]
    sbom = _make_sbom(nodes)
    generator = ScenarioGenerator(sbom)
    scenarios = generator._exfiltration_scenarios()

    probe_titles = [s.title for s in scenarios if "Datastore Schema Probe" in s.title]
    assert len(probe_titles) == 1, f"expected exactly one probe, got {probe_titles}"
    assert "Sqlalchemy" in probe_titles[0]


def test_distinct_datastore_categories_each_get_one_scenario() -> None:
    """A relational DB, a vector store, and a KV cache are genuinely distinct
    attack surfaces — each category should still be probed once."""
    nodes = [
        _agent_node(),
        _datastore_node("Postgresql", "relational", 0.9),
        _datastore_node("Qdrantclient", "vector", 0.85),
        _datastore_node("Redis", "kv", 0.8),
    ]
    sbom = _make_sbom(nodes)
    generator = ScenarioGenerator(sbom)
    scenarios = generator._exfiltration_scenarios()

    probe_titles = [s.title for s in scenarios if "Datastore Schema Probe" in s.title]
    assert len(probe_titles) == 3
    assert any("Postgresql" in t for t in probe_titles)
    assert any("Qdrantclient" in t for t in probe_titles)
    assert any("Redis" in t for t in probe_titles)
