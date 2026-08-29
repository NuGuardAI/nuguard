"""Integration test for the sparkflows.io no-code/low-code sample app.

Runs the real ``AiSbomExtractor`` end-to-end against the checked-in sample
project at ``tests/apps/sparkflows/src/Automated-Order-Review-with-Anomaly-Detection``
(exported from the sparkflows IDE — an agent graph, several ETL/Spark
workflows with Salesforce and JDBC nodes, an H2O Isolation Forest training
pipeline, dataset definitions, and a deployed analytics app).

This asserts the sparkflows adapters (``nuguard/sbom/adapters/sparkflows.py``)
produce FRAMEWORK/AGENT/PROMPT/MODEL/TOOL/DATASTORE nodes with real
file:line evidence, and specifically that a workflow's own identity node and
the agent-side ``workflow_execution``/tool-call node referencing it by uuid
merge into a single TOOL node via NuGuard's canonical-name dedup — the
mechanism this project uses in place of a full cross-file relationship
graph (deferred as follow-up work).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.models import AiSbomDocument
from nuguard.sbom.types import ComponentType

FIXTURE = (
    Path(__file__).parents[3]
    / "tests"
    / "apps"
    / "sparkflows"
    / "src"
    / "Automated-Order-Review-with-Anomaly-Detection"
)

SPARKFLOWS_JSON_ONLY = AiSbomConfig(include_extensions={".json"}, enable_llm=False)

pytestmark = pytest.mark.skipif(not FIXTURE.is_dir(), reason=f"fixture not found: {FIXTURE}")


@pytest.fixture(scope="module")
def doc() -> AiSbomDocument:
    return AiSbomExtractor().extract_from_path(FIXTURE, SPARKFLOWS_JSON_ONLY)


def _nodes(doc: AiSbomDocument, typ: ComponentType) -> list:
    return [n for n in doc.nodes if n.component_type == typ]


def _assert_has_evidence(node) -> None:
    assert node.evidence, f"{node.component_type} {node.name!r} has no evidence"
    for ev in node.evidence:
        assert ev.location.path.endswith(".json"), ev.location.path
        assert ev.location.line is not None and ev.location.line > 0, ev.location


class TestFrameworkDetection:
    def test_sparkflows_framework_detected(self, doc: AiSbomDocument) -> None:
        frameworks = nodes = _nodes(doc, ComponentType.FRAMEWORK)
        sparkflows = next((n for n in nodes if n.metadata.extras.get("framework") == "sparkflows"), None)
        assert sparkflows is not None, f"frameworks found: {[n.name for n in frameworks]}"
        _assert_has_evidence(sparkflows)

    def test_h2o_framework_detected(self, doc: AiSbomDocument) -> None:
        frameworks = _nodes(doc, ComponentType.FRAMEWORK)
        assert any(n.metadata.extras.get("framework") == "h2o" for n in frameworks)


class TestAgentDetection:
    def test_agent_graph_detected(self, doc: AiSbomDocument) -> None:
        agents = _nodes(doc, ComponentType.AGENT)
        agent = next(
            (n for n in agents if "Order Review" in n.name and "App" not in n.name), None
        )
        assert agent is not None, f"agents found: {[n.name for n in agents]}"
        _assert_has_evidence(agent)

    def test_analytics_app_entry_point_detected(self, doc: AiSbomDocument) -> None:
        agents = _nodes(doc, ComponentType.AGENT)
        app = next((n for n in agents if n.metadata.extras.get("agent_type") == "analytics_app"), None)
        assert app is not None, f"agents found: {[n.name for n in agents]}"
        assert app.name == "Order Review App"


class TestPromptDetection:
    def test_system_prompt_extracted_with_content(self, doc: AiSbomDocument) -> None:
        prompts = _nodes(doc, ComponentType.PROMPT)
        assert prompts, "expected at least one PROMPT node"
        validation_prompt = next((n for n in prompts if n.name == "Validate Invoicing Fields"), None)
        assert validation_prompt is not None, f"prompts found: {[n.name for n in prompts]}"
        assert "invoicing" in validation_prompt.metadata.extras.get("content", "").lower()
        _assert_has_evidence(validation_prompt)


class TestModelDetection:
    def test_llm_connection_placeholder_detected(self, doc: AiSbomDocument) -> None:
        models = _nodes(doc, ComponentType.MODEL)
        placeholder = next((n for n in models if n.metadata.extras.get("resolved") is False), None)
        assert placeholder is not None, f"models found: {[n.name for n in models]}"
        assert placeholder.metadata.extras.get("provider") == "unknown"

    def test_h2o_model_detected(self, doc: AiSbomDocument) -> None:
        models = _nodes(doc, ComponentType.MODEL)
        h2o = next((n for n in models if n.metadata.extras.get("framework") == "h2o"), None)
        assert h2o is not None, f"models found: {[n.name for n in models]}"
        _assert_has_evidence(h2o)


class TestDatastoreDetection:
    def test_salesforce_datastore_detected(self, doc: AiSbomDocument) -> None:
        datastores = _nodes(doc, ComponentType.DATASTORE)
        salesforce = next((n for n in datastores if n.metadata.extras.get("provider") == "salesforce"), None)
        assert salesforce is not None, f"datastores found: {[n.name for n in datastores]}"
        _assert_has_evidence(salesforce)

    def test_jdbc_mysql_datastore_merges_workflow_and_dataset_evidence(
        self, doc: AiSbomDocument
    ) -> None:
        datastores = _nodes(doc, ComponentType.DATASTORE)
        jdbc = next((n for n in datastores if n.metadata.extras.get("provider") == "jdbc" and "connection_id" not in n.metadata.extras), None)
        assert jdbc is not None, f"datastores found: {[n.name for n in datastores]}"
        paths = {ev.location.path for ev in jdbc.evidence}
        assert any(p.startswith("datasets/") for p in paths)
        assert any(p.startswith("workflows/") for p in paths)


class TestToolCrossFileMerge:
    def test_workflow_execution_reference_merges_with_workflow_identity(
        self, doc: AiSbomDocument
    ) -> None:
        tools = _nodes(doc, ComponentType.TOOL)
        merged = next(
            (
                n
                for n in tools
                if any(ev.location.path.startswith("agents/") for ev in n.evidence)
                and any(ev.location.path.startswith("workflows/") for ev in n.evidence)
            ),
            None,
        )
        assert merged is not None, (
            "expected a TOOL node with evidence from both an agents/ and a "
            f"workflows/ file (canonical dedup merge); tools found: {[t.name for t in tools]}"
        )


class TestOutOfScopeArtifacts:
    def test_no_nodes_from_charts_dashboards_or_wikidocs(self, doc: AiSbomDocument) -> None:
        for node in doc.nodes:
            for ev in node.evidence:
                path = ev.location.path
                assert not path.startswith("charts/"), path
                assert not path.startswith("dashboards/"), path
                assert not path.startswith("wikiDocs/"), path
