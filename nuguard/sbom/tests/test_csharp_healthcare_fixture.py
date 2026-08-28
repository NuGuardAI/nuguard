"""Integration test for the csharp_healthcare_service fixture.

Before this test was added, C# support was implemented at the
parser/adapter level but never wired into ``AiSbomExtractor`` — no
``_CSHARP_EXTENSIONS``, no dispatch branch in ``extractor/core.py``, and no
import of the ``csharp`` adapter package in ``adapters/registry.py``. A
real scan of a C#/.NET repo therefore produced zero structural
FRAMEWORK/API_ENDPOINT/MODEL/TOOL/PROMPT nodes, even though
``test_csharp_framework_adapters.py`` passed (it calls each adapter's
``.extract()`` directly, bypassing the pipeline entirely). This test
exercises the real end-to-end pipeline, mirroring
``test_go_healthcare_fixture.py``, so that class of regression is caught.
"""

from __future__ import annotations

import pytest

from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.models import AiSbomDocument
from nuguard.sbom.tests.conftest import APPS, CS_ONLY, nodes
from nuguard.sbom.types import ComponentType

FIXTURE = APPS / "csharp_healthcare_service"


@pytest.fixture(scope="module")
def doc() -> AiSbomDocument:
    return AiSbomExtractor().extract_from_path(FIXTURE, CS_ONLY)


def _assert_has_evidence(node) -> None:
    """Every node must carry >=1 Evidence entry with a real file path and line."""
    assert node.evidence, f"{node.component_type} {node.name!r} has no evidence"
    for ev in node.evidence:
        assert ev.location.path.endswith(".cs"), ev.location.path
        assert ev.location.line is not None and ev.location.line > 0, ev.location


class TestFrameworkDetection:
    def test_aspnet_core_framework_detected(self, doc: AiSbomDocument) -> None:
        frameworks = nodes(doc, ComponentType.FRAMEWORK)
        assert any(n.metadata.extras.get("language") == "csharp" for n in frameworks)
        aspnet = next(
            (n for n in frameworks if n.metadata.extras.get("adapter") == "csharp_aspnet_core"),
            None,
        )
        assert aspnet is not None, f"frameworks found: {[n.name for n in frameworks]}"
        _assert_has_evidence(aspnet)

    def test_openai_framework_detected(self, doc: AiSbomDocument) -> None:
        frameworks = nodes(doc, ComponentType.FRAMEWORK)
        assert any(n.metadata.extras.get("provider") == "openai" for n in frameworks)

    def test_semantic_kernel_framework_detected(self, doc: AiSbomDocument) -> None:
        frameworks = nodes(doc, ComponentType.FRAMEWORK)
        assert any(
            n.metadata.extras.get("adapter") == "csharp_semantic_kernel" for n in frameworks
        )


class TestEndpointDetection:
    def test_controller_routes_become_api_endpoint_nodes(self, doc: AiSbomDocument) -> None:
        endpoints = nodes(doc, ComponentType.API_ENDPOINT)
        found = {(n.metadata.extras.get("method"), n.metadata.extras.get("endpoint")) for n in endpoints}
        assert ("POST", "/api/Chat/complete") in found
        assert ("GET", "/api/Chat/{id}/triage") in found
        for n in endpoints:
            _assert_has_evidence(n)


class TestModelDetection:
    def test_openai_model_constructor_arg_extracted_with_evidence(
        self, doc: AiSbomDocument
    ) -> None:
        models = nodes(doc, ComponentType.MODEL)
        gpt4 = next((n for n in models if n.name.lower() == "gpt-4-turbo"), None)
        assert gpt4 is not None, f"models found: {[n.name for n in models]}"
        _assert_has_evidence(gpt4)

    def test_semantic_kernel_deployment_model_extracted(self, doc: AiSbomDocument) -> None:
        models = nodes(doc, ComponentType.MODEL)
        gpt4o = next((n for n in models if n.name.lower() == "gpt-4o"), None)
        assert gpt4o is not None, f"models found: {[n.name for n in models]}"
        _assert_has_evidence(gpt4o)


class TestToolDetection:
    def test_semantic_kernel_plugin_becomes_tool_node(self, doc: AiSbomDocument) -> None:
        tools = nodes(doc, ComponentType.TOOL)
        names = {n.name for n in tools}
        assert "GetWeather" in names or "Weather" in names, f"tools found: {names}"
