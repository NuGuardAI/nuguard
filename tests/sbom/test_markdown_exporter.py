"""Test MarkdownExporterPlugin output structure."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nuguard.models.sbom import (
    AiSbomDocument,
    DataClassification,
    DatastoreType,
    Node,
    NodeMetadata,
    NodeType,
    PackageDep,
    ScanSummary,
)
from nuguard.sbom.toolbox.plugins.markdown_exporter import MarkdownExporterPlugin


def _doc(**kwargs) -> AiSbomDocument:
    return AiSbomDocument(
        generated_at=datetime.now(timezone.utc),
        target="/test/my-ai-app",
        summary=ScanSummary(
            node_counts={"AGENT": 1, "TOOL": 2},
            frameworks=["langchain"],
        ),
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
def plugin() -> MarkdownExporterPlugin:
    return MarkdownExporterPlugin()


@pytest.fixture
def sample_doc() -> AiSbomDocument:
    agent = _node("my_agent", NodeType.AGENT, framework="langchain")
    tool = _node("search_tool", NodeType.TOOL, framework="langchain")
    ds = _node("users_db", NodeType.DATASTORE,
               datastore_type=DatastoreType.RELATIONAL,
               data_classification=[DataClassification.PII])
    ep = _node("GET:/users", NodeType.API_ENDPOINT, endpoint="/users", method="GET")
    img = _node("myapp", NodeType.CONTAINER_IMAGE, runs_as_root=True)
    dep = PackageDep(name="langchain", version_spec=">=0.1.0", ecosystem="pypi")
    return _doc(
        nodes=[agent, tool, ds, ep, img],
        deps=[dep],
    )


def test_markdown_starts_with_title(plugin: MarkdownExporterPlugin, sample_doc: AiSbomDocument) -> None:
    """Report starts with '# NuGuard AI Security Report'."""
    result = plugin.run(sample_doc)
    md = result.details[0]["markdown"]
    assert md.startswith("# NuGuard AI Security Report")


def test_markdown_contains_summary(plugin: MarkdownExporterPlugin, sample_doc: AiSbomDocument) -> None:
    """Report contains Summary section."""
    result = plugin.run(sample_doc)
    md = result.details[0]["markdown"]
    assert "## Summary" in md
    assert "/test/my-ai-app" in md


def test_markdown_contains_agents_section(plugin: MarkdownExporterPlugin, sample_doc: AiSbomDocument) -> None:
    """Report has an Agents section with agent names."""
    result = plugin.run(sample_doc)
    md = result.details[0]["markdown"]
    assert "### Agents" in md
    assert "my_agent" in md


def test_markdown_contains_datastores_section(plugin: MarkdownExporterPlugin, sample_doc: AiSbomDocument) -> None:
    """Report has a Datastores section."""
    result = plugin.run(sample_doc)
    md = result.details[0]["markdown"]
    assert "### Datastores" in md
    assert "users_db" in md


def test_markdown_contains_api_endpoints(plugin: MarkdownExporterPlugin, sample_doc: AiSbomDocument) -> None:
    """Report has API Endpoints section when endpoints present."""
    result = plugin.run(sample_doc)
    md = result.details[0]["markdown"]
    assert "### API Endpoints" in md
    assert "GET" in md


def test_markdown_contains_security_findings(plugin: MarkdownExporterPlugin, sample_doc: AiSbomDocument) -> None:
    """Report contains Security Findings section."""
    result = plugin.run(sample_doc)
    md = result.details[0]["markdown"]
    assert "## Security Findings" in md


def test_markdown_contains_atlas_section(plugin: MarkdownExporterPlugin, sample_doc: AiSbomDocument) -> None:
    """Report contains MITRE ATLAS Annotations section."""
    result = plugin.run(sample_doc)
    md = result.details[0]["markdown"]
    assert "## MITRE ATLAS Annotations" in md


def test_markdown_contains_dependencies(plugin: MarkdownExporterPlugin, sample_doc: AiSbomDocument) -> None:
    """Report contains Dependencies section when deps present."""
    result = plugin.run(sample_doc)
    md = result.details[0]["markdown"]
    assert "## Dependencies" in md
    assert "langchain" in md


def test_empty_sbom_produces_markdown(plugin: MarkdownExporterPlugin) -> None:
    """Empty SBOM still produces a valid Markdown report."""
    doc = _doc(nodes=[], edges=[])
    result = plugin.run(doc)
    md = result.details[0]["markdown"]
    assert "# NuGuard AI Security Report" in md
    assert result.status == "pass"


def test_markdown_result_status(plugin: MarkdownExporterPlugin, sample_doc: AiSbomDocument) -> None:
    """Plugin returns 'pass' status."""
    result = plugin.run(sample_doc)
    assert result.status == "pass"
