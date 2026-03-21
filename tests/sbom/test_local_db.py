"""Test LocalDb SBOM registry."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nuguard.db.local import LocalDb
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata, ScanSummary
from nuguard.sbom.types import ComponentType


def _make_doc(target: str = "/test/app") -> AiSbomDocument:
    node = Node(
        name="my_agent",
        component_type=ComponentType.AGENT,
        confidence=0.9,
        metadata=NodeMetadata(framework="langchain"),
    )
    return AiSbomDocument(
        target=target,
        nodes=[node],
        summary=ScanSummary(node_counts={"AGENT": 1}),
    )


@pytest.fixture
def db(tmp_path: Path) -> LocalDb:
    """Create a LocalDb backed by a temp directory."""
    db_path = tmp_path / "nuguard.db"
    return LocalDb(path=db_path)


def test_init_schema_creates_db(db: LocalDb) -> None:
    """init_schema creates the database file."""
    db.init_schema()
    assert db.path.exists()


def test_save_sbom_returns_id(db: LocalDb) -> None:
    """save_sbom returns a non-empty string ID."""
    doc = _make_doc()
    sbom_id = db.save_sbom(doc)
    assert isinstance(sbom_id, str)
    assert len(sbom_id) > 0


def test_get_sbom_roundtrip(db: LocalDb) -> None:
    """save_sbom → get_sbom roundtrip preserves the document."""
    doc = _make_doc()
    sbom_id = db.save_sbom(doc)
    retrieved = db.get_sbom(sbom_id)
    assert retrieved is not None
    assert retrieved.target == doc.target
    assert len(retrieved.nodes) == len(doc.nodes)
    assert retrieved.nodes[0].name == "my_agent"


def test_get_nonexistent_sbom_returns_none(db: LocalDb) -> None:
    """get_sbom returns None for unknown ID."""
    db.init_schema()
    result = db.get_sbom("nonexistent-id")
    assert result is None


def test_list_sboms_empty(db: LocalDb) -> None:
    """list_sboms returns empty list when no SBOMs stored."""
    db.init_schema()
    entries = db.list_sboms()
    assert entries == []


def test_list_sboms_after_save(db: LocalDb) -> None:
    """list_sboms returns entries after saving."""
    doc1 = _make_doc(target="/app1")
    doc2 = _make_doc(target="/app2")
    id1 = db.save_sbom(doc1)
    id2 = db.save_sbom(doc2)
    entries = db.list_sboms()
    assert len(entries) == 2
    ids = {e["id"] for e in entries}
    assert id1 in ids
    assert id2 in ids


def test_list_sboms_contains_expected_fields(db: LocalDb) -> None:
    """Each list entry has id, target, generated_at, node_count fields."""
    doc = _make_doc()
    db.save_sbom(doc)
    entries = db.list_sboms()
    entry = entries[0]
    assert "id" in entry
    assert "target" in entry
    assert "generated_at" in entry
    assert "node_count" in entry
    assert entry["node_count"] == 1


def test_list_sboms_node_count(db: LocalDb) -> None:
    """node_count in list entry matches number of nodes in doc."""
    doc = _make_doc()
    db.save_sbom(doc)
    entries = db.list_sboms()
    assert entries[0]["node_count"] == len(doc.nodes)


def test_multiple_saves_multiple_ids(db: LocalDb) -> None:
    """Saving the same doc twice produces different IDs."""
    doc = _make_doc()
    id1 = db.save_sbom(doc)
    id2 = db.save_sbom(doc)
    assert id1 != id2
