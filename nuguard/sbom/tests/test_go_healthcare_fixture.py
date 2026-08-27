"""Integration test for the go_healthcare_service fixture (phases 5-6 validation).

This fixture is modeled on the shape of mosaic-care/healthcare-service — a
Golang backend with a gin HTTP router, a go-openai LLM client, a MongoDB
datastore, JWT auth, and a package-level system-prompt constant — the exact
combination that, before docs/go-support.md's phases 2-4 (and the prompt
constant added for phase 6), produced *zero* file-anchored SBOM nodes (only
bare-word regex matches with no evidence, and no content at all for the
prompt). This test asserts the full extraction pipeline now produces
FRAMEWORK/API_ENDPOINT/MODEL/DATASTORE/AUTH/PROMPT nodes, each with real
file:line evidence tying it back to the exact construction/call site.
"""

from __future__ import annotations

import pytest

from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.models import AiSbomDocument
from nuguard.sbom.tests.conftest import APPS, GO_ONLY, nodes
from nuguard.sbom.types import ComponentType

FIXTURE = APPS / "go_healthcare_service"


@pytest.fixture(scope="module")
def doc() -> AiSbomDocument:
    return AiSbomExtractor().extract_from_path(FIXTURE, GO_ONLY)


def _assert_has_evidence(node) -> None:
    """Every node must carry >=1 Evidence entry with a real file path and line."""
    assert node.evidence, f"{node.component_type} {node.name!r} has no evidence"
    for ev in node.evidence:
        assert ev.location.path.endswith(".go"), ev.location.path
        assert ev.location.line is not None and ev.location.line > 0, ev.location


class TestFrameworkDetection:
    def test_gin_framework_detected(self, doc: AiSbomDocument) -> None:
        frameworks = {n.name.lower(): n for n in nodes(doc, ComponentType.FRAMEWORK)}
        assert "gin" in frameworks
        _assert_has_evidence(frameworks["gin"])

    def test_go_openai_framework_detected(self, doc: AiSbomDocument) -> None:
        frameworks = nodes(doc, ComponentType.FRAMEWORK)
        assert any(n.metadata.extras.get("framework") == "go_openai" for n in frameworks)


class TestEndpointDetection:
    def test_gin_routes_become_api_endpoint_nodes(self, doc: AiSbomDocument) -> None:
        endpoints = nodes(doc, ComponentType.API_ENDPOINT)
        found = {(n.metadata.extras.get("method"), n.metadata.extras.get("endpoint")) for n in endpoints}
        assert ("GET", "/patients/:id") in found
        assert ("POST", "/patients/:id/triage") in found
        for n in endpoints:
            _assert_has_evidence(n)


class TestModelDetection:
    def test_go_openai_model_field_extracted_with_evidence(self, doc: AiSbomDocument) -> None:
        models = nodes(doc, ComponentType.MODEL)
        gpt4 = next((n for n in models if n.name.lower() == "gpt-4-turbo"), None)
        assert gpt4 is not None, f"models found: {[n.name for n in models]}"
        _assert_has_evidence(gpt4)


class TestDatastoreDetection:
    def test_mongo_connect_becomes_datastore_node(self, doc: AiSbomDocument) -> None:
        datastores = nodes(doc, ComponentType.DATASTORE)
        mongo = next((n for n in datastores if n.metadata.extras.get("provider") == "mongodb"), None)
        assert mongo is not None, f"datastores found: {[n.name for n in datastores]}"
        _assert_has_evidence(mongo)


class TestAuthDetection:
    def test_jwt_sign_call_becomes_auth_node(self, doc: AiSbomDocument) -> None:
        auth_nodes = nodes(doc, ComponentType.AUTH)
        jwt_node = next((n for n in auth_nodes if n.metadata.extras.get("auth_type") == "jwt"), None)
        assert jwt_node is not None, f"auth nodes found: {[n.name for n in auth_nodes]}"
        _assert_has_evidence(jwt_node)


class TestPromptDetection:
    def test_system_prompt_constant_becomes_prompt_node_with_content(
        self, doc: AiSbomDocument
    ) -> None:
        prompts = nodes(doc, ComponentType.PROMPT)
        # Display names go through normalize_display_name in postprocessing
        # (e.g. "systemPrompt" -> "Systemprompt"), so match case-insensitively.
        system_prompt = next((n for n in prompts if n.name.lower() == "systemprompt"), None)
        assert system_prompt is not None, f"prompts found: {[n.name for n in prompts]}"
        assert system_prompt.metadata.extras.get("role") == "system"
        content = system_prompt.metadata.extras.get("content", "")
        assert "Mosaic's health assistant" in content
        _assert_has_evidence(system_prompt)
