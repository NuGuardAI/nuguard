"""Test SarifExporterPlugin SARIF 2.1.0 output structure."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nuguard.models.sbom import (
    AiSbomDocument,
    Node,
    NodeMetadata,
    NodeType,
    ScanSummary,
)
from nuguard.sbom.toolbox.plugins.sarif_exporter import SarifExporterPlugin


def _doc(**kwargs) -> AiSbomDocument:
    return AiSbomDocument(
        generated_at=datetime.now(timezone.utc),
        target="/test/project",
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
def plugin() -> SarifExporterPlugin:
    return SarifExporterPlugin()


def test_sarif_schema_field(plugin: SarifExporterPlugin) -> None:
    """SARIF output must have $schema field."""
    doc = _doc(nodes=[], edges=[])
    result = plugin.run(doc)
    sarif = result.details[0]["sarif"]
    assert "$schema" in sarif
    assert "sarif" in sarif["$schema"].lower()


def test_sarif_version(plugin: SarifExporterPlugin) -> None:
    """SARIF version must be '2.1.0'."""
    doc = _doc(nodes=[], edges=[])
    result = plugin.run(doc)
    sarif = result.details[0]["sarif"]
    assert sarif["version"] == "2.1.0"


def test_sarif_tool_name(plugin: SarifExporterPlugin) -> None:
    """Tool driver name must be 'nuguard'."""
    doc = _doc(nodes=[], edges=[])
    result = plugin.run(doc)
    sarif = result.details[0]["sarif"]
    driver = sarif["runs"][0]["tool"]["driver"]
    assert driver["name"] == "nuguard"


def test_sarif_has_runs(plugin: SarifExporterPlugin) -> None:
    """SARIF output must have runs array."""
    doc = _doc(nodes=[], edges=[])
    result = plugin.run(doc)
    sarif = result.details[0]["sarif"]
    assert "runs" in sarif
    assert len(sarif["runs"]) == 1


def test_sarif_results_from_findings(plugin: SarifExporterPlugin) -> None:
    """VLA findings are converted to SARIF results."""
    img = _node("root-app", NodeType.CONTAINER_IMAGE, runs_as_root=True)
    doc = _doc(nodes=[img], edges=[])
    result = plugin.run(doc)
    sarif = result.details[0]["sarif"]
    results = sarif["runs"][0]["results"]
    assert len(results) >= 1


def test_sarif_result_level_mapping(plugin: SarifExporterPlugin) -> None:
    """High severity findings map to 'error' level."""
    img = _node("root-app", NodeType.CONTAINER_IMAGE, runs_as_root=True)
    doc = _doc(nodes=[img], edges=[])
    result = plugin.run(doc)
    sarif = result.details[0]["sarif"]
    results = sarif["runs"][0]["results"]
    # VLA-005 is high → error
    high_results = [r for r in results if r["ruleId"] == "VLA-005"]
    assert len(high_results) >= 1
    assert high_results[0]["level"] == "error"


def test_sarif_rules_populated(plugin: SarifExporterPlugin) -> None:
    """Rules array is populated from findings."""
    ep = _node("GET:/open", NodeType.API_ENDPOINT, endpoint="/open")
    doc = _doc(nodes=[ep], edges=[])
    result = plugin.run(doc)
    sarif = result.details[0]["sarif"]
    rules = sarif["runs"][0]["tool"]["driver"]["rules"]
    assert len(rules) >= 1
    for rule in rules:
        assert "id" in rule
        assert "shortDescription" in rule


def test_empty_sbom_produces_valid_sarif(plugin: SarifExporterPlugin) -> None:
    """Empty SBOM still produces valid SARIF structure."""
    doc = _doc(nodes=[], edges=[])
    result = plugin.run(doc)
    sarif = result.details[0]["sarif"]
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
