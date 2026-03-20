"""Test PluginOrchestrator."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nuguard.models.sbom import AiSbomDocument, Node, NodeMetadata, NodeType, ScanSummary
from nuguard.sbom.toolbox.orchestrator import PLUGIN_REGISTRY, PluginOrchestrator  # noqa: F401


def _empty_doc() -> AiSbomDocument:
    return AiSbomDocument(
        generated_at=datetime.now(timezone.utc),
        target="test",
        summary=ScanSummary(),
    )


@pytest.fixture
def orchestrator() -> PluginOrchestrator:
    return PluginOrchestrator()


def test_list_plugins(orchestrator: PluginOrchestrator) -> None:
    """list_plugins returns all registered plugin names."""
    plugins = orchestrator.list_plugins()
    assert "vulnerability" in plugins
    assert "atlas" in plugins
    assert "sarif" in plugins
    assert "markdown" in plugins
    assert "license" in plugins
    assert "dependency" in plugins


def test_run_unknown_plugin_raises(orchestrator: PluginOrchestrator) -> None:
    """Running an unknown plugin raises ValueError."""
    doc = _empty_doc()
    with pytest.raises(ValueError, match="Unknown plugin"):
        orchestrator.run("nonexistent_plugin", doc)


def test_run_vulnerability_plugin(orchestrator: PluginOrchestrator) -> None:
    """vulnerability plugin runs without error."""
    doc = _empty_doc()
    result = orchestrator.run("vulnerability", doc)
    assert result.status == "pass"


def test_run_atlas_plugin(orchestrator: PluginOrchestrator) -> None:
    """atlas plugin runs without error."""
    doc = _empty_doc()
    result = orchestrator.run("atlas", doc)
    assert result.status == "pass"


def test_run_sarif_plugin(orchestrator: PluginOrchestrator) -> None:
    """sarif plugin runs and returns SARIF structure."""
    doc = _empty_doc()
    result = orchestrator.run("sarif", doc)
    assert result.details[0]["sarif"]["version"] == "2.1.0"


def test_run_markdown_plugin(orchestrator: PluginOrchestrator) -> None:
    """markdown plugin runs and returns markdown string."""
    doc = _empty_doc()
    result = orchestrator.run("markdown", doc)
    md = result.details[0]["markdown"]
    assert "NuGuard" in md


def test_run_all_returns_all_plugins(orchestrator: PluginOrchestrator) -> None:
    """run_all returns a result for every registered plugin."""
    doc = _empty_doc()
    results = orchestrator.run_all(doc)
    for plugin_name in PLUGIN_REGISTRY:
        assert plugin_name in results


def test_run_all_no_exception(orchestrator: PluginOrchestrator) -> None:
    """run_all completes without raising even on empty SBOM."""
    doc = _empty_doc()
    results = orchestrator.run_all(doc)
    assert len(results) == len(PLUGIN_REGISTRY)


def test_run_plugin_with_dict_sbom(orchestrator: PluginOrchestrator) -> None:
    """Plugins accept plain dict as SBOM input (backward compat)."""
    doc = _empty_doc()
    sbom_dict = doc.model_dump(mode="json")
    result = orchestrator.run("vulnerability", sbom_dict)
    assert result.status == "pass"
