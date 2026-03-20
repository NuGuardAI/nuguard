"""Tests for CycloneDX 1.6 output from AiSbomSerializer.

Validates that the enhanced serializer correctly maps AI components to
CycloneDX types, emits model card URLs as externalReferences, attaches
pkg:pypi/ PURLs for package dependencies, and satisfies structural
requirements for a valid CycloneDX 1.6 BOM.

Fixtures are the real-world app directories used in test_scenarios.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from xelo.config import AiSbomConfig
from xelo.deps import DependencyScanner, PackageDep
from xelo.extractor import AiSbomExtractor
from xelo.models import AiSbomDocument
from xelo.serializer import AiSbomSerializer
from xelo.types import ComponentType

_APPS = Path(__file__).parent / "fixtures" / "apps"
_PY_ONLY = AiSbomConfig(include_extensions={".py"}, enable_llm=False)


def _extract(app: str) -> AiSbomDocument:
    return AiSbomExtractor().extract_from_path(_APPS / app, _PY_ONLY)


def _cdx(app: str, deps: list[PackageDep] | None = None) -> dict[str, Any]:
    return AiSbomSerializer.to_cyclonedx(_extract(app), deps=deps)


# ---------------------------------------------------------------------------
# Top-level BOM structure
# ---------------------------------------------------------------------------


class TestBomStructure:
    @pytest.fixture(scope="class")
    def bom(self) -> dict[str, Any]:
        return _cdx("customer_service_bot")

    def test_bom_format(self, bom: dict[str, Any]) -> None:
        assert bom["bomFormat"] == "CycloneDX"

    def test_spec_version(self, bom: dict[str, Any]) -> None:
        assert bom["specVersion"] == "1.6"

    def test_version_integer(self, bom: dict[str, Any]) -> None:
        assert isinstance(bom["version"], int)
        assert bom["version"] >= 1

    def test_serial_number_format(self, bom: dict[str, Any]) -> None:
        sn = bom.get("serialNumber", "")
        assert sn.startswith("urn:uuid:"), f"serialNumber should start with urn:uuid:, got {sn!r}"

    def test_metadata_present(self, bom: dict[str, Any]) -> None:
        assert "metadata" in bom
        meta = bom["metadata"]
        assert "timestamp" in meta
        assert "tools" in meta
        assert "component" in meta

    def test_timestamp_iso_format(self, bom: dict[str, Any]) -> None:
        ts = bom["metadata"]["timestamp"]
        # ISO 8601 pattern
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts), (
            f"Timestamp not ISO 8601: {ts!r}"
        )

    def test_tool_info(self, bom: dict[str, Any]) -> None:
        tools = bom["metadata"]["tools"]
        assert tools
        tool = tools[0]
        assert tool.get("vendor") == "Xelo"
        assert tool.get("name")
        assert tool.get("version")

    def test_target_component(self, bom: dict[str, Any]) -> None:
        comp = bom["metadata"]["component"]
        assert comp["type"] == "application"
        assert comp["name"]

    def test_components_list_present(self, bom: dict[str, Any]) -> None:
        assert "components" in bom
        assert isinstance(bom["components"], list)
        assert len(bom["components"]) > 0

    def test_dependencies_list_present(self, bom: dict[str, Any]) -> None:
        assert "dependencies" in bom
        assert isinstance(bom["dependencies"], list)


# ---------------------------------------------------------------------------
# AI component type mapping
# ---------------------------------------------------------------------------


class TestAiComponentTypes:
    """Validate CycloneDX type mapping for AI component types."""

    @pytest.fixture(scope="class")
    def doc(self) -> AiSbomDocument:
        return _extract("customer_service_bot")

    @pytest.fixture(scope="class")
    def bom(self, doc: AiSbomDocument) -> dict[str, Any]:
        return AiSbomSerializer.to_cyclonedx(doc)

    def test_model_nodes_map_to_ml_model_type(
        self, doc: AiSbomDocument, bom: dict[str, Any]
    ) -> None:
        model_names = {n.name for n in doc.nodes if n.component_type == ComponentType.MODEL}
        ml_model_comps = [c for c in bom["components"] if c["type"] == "machine-learning-model"]
        ml_model_names = {c["name"] for c in ml_model_comps}
        for name in model_names:
            assert name in ml_model_names, (
                f"MODEL node {name!r} not mapped to machine-learning-model"
            )

    def test_agent_nodes_map_to_application(self, doc: AiSbomDocument, bom: dict[str, Any]) -> None:
        agent_names = {n.name for n in doc.nodes if n.component_type == ComponentType.AGENT}
        app_comps = {c["name"] for c in bom["components"] if c["type"] == "application"}
        for name in agent_names:
            assert name in app_comps, f"AGENT node {name!r} not mapped to application"

    def test_framework_nodes_map_to_application(
        self, doc: AiSbomDocument, bom: dict[str, Any]
    ) -> None:
        fw_names = {n.name for n in doc.nodes if n.component_type == ComponentType.FRAMEWORK}
        app_comps = {c["name"] for c in bom["components"] if c["type"] == "application"}
        for name in fw_names:
            assert name in app_comps, f"FRAMEWORK node {name!r} not mapped to application"

    def test_tool_nodes_map_to_library(self, doc: AiSbomDocument, bom: dict[str, Any]) -> None:
        tool_names = {n.name for n in doc.nodes if n.component_type == ComponentType.TOOL}
        lib_comps = {c["name"] for c in bom["components"] if c["type"] == "library"}
        for name in tool_names:
            assert name in lib_comps, f"TOOL node {name!r} not mapped to library"

    def test_no_unknown_types(self, bom: dict[str, Any]) -> None:
        valid_types = {
            "application",
            "library",
            "machine-learning-model",
            "data",
            "container",
            "firmware",
            "device",
            "file",
        }
        for comp in bom["components"]:
            assert comp["type"] in valid_types, f"Unknown CycloneDX type: {comp['type']!r}"


# ---------------------------------------------------------------------------
# MODEL externalReferences (model card URLs)
# ---------------------------------------------------------------------------


class TestModelExternalReferences:
    @pytest.fixture(scope="class")
    def bom(self) -> dict[str, Any]:
        return _cdx("customer_service_bot")

    def test_ml_model_has_external_references(self, bom: dict[str, Any]) -> None:
        ml_comps = [c for c in bom["components"] if c["type"] == "machine-learning-model"]
        assert ml_comps, "Need at least one ML model component"
        enriched = [c for c in ml_comps if c.get("externalReferences")]
        assert enriched, (
            "Expected at least one machine-learning-model with externalReferences "
            f"(model card URL). Components: {[c['name'] for c in ml_comps]}"
        )

    def test_model_card_ref_has_documentation_type(self, bom: dict[str, Any]) -> None:
        ml_comps = [c for c in bom["components"] if c["type"] == "machine-learning-model"]
        for comp in ml_comps:
            for ref in comp.get("externalReferences", []):
                assert ref.get("type") in {"documentation", "website"}, (
                    f"Unexpected externalReference type: {ref.get('type')!r}"
                )
                assert ref.get("url"), "externalReference missing url"

    def test_openai_model_card_url_domain(self, bom: dict[str, Any]) -> None:
        ml_comps = [c for c in bom["components"] if c["type"] == "machine-learning-model"]
        openai_props = [
            c
            for c in ml_comps
            if any(
                p.get("name") == "xelo:provider" and p.get("value") == "openai"
                for p in c.get("properties", [])
            )
        ]
        for comp in openai_props:
            urls = [r["url"] for r in comp.get("externalReferences", [])]
            if urls:
                assert any("openai.com" in u for u in urls), (
                    f"OpenAI model card URL should contain openai.com: {urls}"
                )

    def test_anthropic_model_card_url_domain(self, bom: dict[str, Any]) -> None:
        ml_comps = [c for c in bom["components"] if c["type"] == "machine-learning-model"]
        anthropic_props = [
            c
            for c in ml_comps
            if any(
                p.get("name") == "xelo:provider" and p.get("value") == "anthropic"
                for p in c.get("properties", [])
            )
        ]
        for comp in anthropic_props:
            urls = [r["url"] for r in comp.get("externalReferences", [])]
            if urls:
                assert any("anthropic" in u.lower() for u in urls), (
                    f"Anthropic model card URL should contain 'anthropic': {urls}"
                )


# ---------------------------------------------------------------------------
# Xelo properties on AI components
# ---------------------------------------------------------------------------


class TestVelaProperties:
    @pytest.fixture(scope="class")
    def bom(self) -> dict[str, Any]:
        return _cdx("research_assistant")

    def test_all_components_have_component_type_property(self, bom: dict[str, Any]) -> None:
        # Only AI components carry xelo:component_type; dep library components have purl instead
        ai_comps = [c for c in bom["components"] if not c.get("purl")]
        for comp in ai_comps:
            props = {p["name"]: p["value"] for p in comp.get("properties", [])}
            assert "xelo:component_type" in props, (
                f"Component {comp['name']!r} missing xelo:component_type property"
            )

    def test_all_components_have_confidence_property(self, bom: dict[str, Any]) -> None:
        # Only AI components carry xelo:confidence; dep library components do not
        ai_comps = [c for c in bom["components"] if not c.get("purl")]
        for comp in ai_comps:
            props = {p["name"]: p["value"] for p in comp.get("properties", [])}
            assert "xelo:confidence" in props, (
                f"Component {comp['name']!r} missing xelo:confidence property"
            )
            confidence = float(props["xelo:confidence"])
            assert 0.0 < confidence <= 1.0

    def test_model_components_have_provider_property(self, bom: dict[str, Any]) -> None:
        ml_comps = [c for c in bom["components"] if c["type"] == "machine-learning-model"]
        # AST-enriched models should have provider; at least one must be present
        enriched = [
            c
            for c in ml_comps
            if any(p["name"] == "xelo:provider" for p in c.get("properties", []))
        ]
        assert enriched, (
            f"Expected at least one ML model with xelo:provider property; "
            f"got models: {[c['name'] for c in ml_comps]}"
        )

    def test_bom_ref_matches_node_id(self, bom: dict[str, Any]) -> None:
        for comp in bom["components"]:
            assert comp.get("bom-ref"), f"Component {comp['name']!r} missing bom-ref"


# ---------------------------------------------------------------------------
# Package dependency components (pkg:pypi/ PURLs)
# ---------------------------------------------------------------------------


class TestDepComponents:
    @pytest.fixture(scope="class")
    def scanner(self) -> DependencyScanner:
        return DependencyScanner()

    @pytest.fixture(scope="class")
    def bom_with_deps(self, scanner: DependencyScanner) -> dict[str, Any]:
        doc = _extract("customer_service_bot")
        deps = scanner.scan(_APPS / "customer_service_bot")
        return AiSbomSerializer.to_cyclonedx(doc, deps=deps)

    def test_dep_components_present(self, bom_with_deps: dict[str, Any]) -> None:
        lib_comps = [c for c in bom_with_deps["components"] if c["type"] == "library"]
        assert lib_comps, "Expected library components from package dependencies"

    def test_dep_components_have_purls(self, bom_with_deps: dict[str, Any]) -> None:
        dep_comps = [
            c for c in bom_with_deps["components"] if c.get("purl", "").startswith("pkg:pypi/")
        ]
        assert dep_comps, "Expected dep components with pkg:pypi/ PURLs"

    def test_dep_purl_format(self, bom_with_deps: dict[str, Any]) -> None:
        for comp in bom_with_deps["components"]:
            purl = comp.get("purl", "")
            if purl:
                assert purl.startswith("pkg:pypi/"), f"Invalid PURL scheme: {purl!r}"
                assert comp["name"] in purl, f"PURL {purl!r} missing package name"

    def test_dep_has_dep_group_property(self, bom_with_deps: dict[str, Any]) -> None:
        dep_comps = [c for c in bom_with_deps["components"] if c.get("purl")]
        for comp in dep_comps:
            prop_names = {p["name"] for p in comp.get("properties", [])}
            assert "xelo:dep_group" in prop_names, (
                f"Dep {comp['name']!r} missing xelo:dep_group property"
            )

    def test_dep_has_source_file_property(self, bom_with_deps: dict[str, Any]) -> None:
        dep_comps = [c for c in bom_with_deps["components"] if c.get("purl")]
        for comp in dep_comps:
            prop_names = {p["name"] for p in comp.get("properties", [])}
            assert "xelo:source_file" in prop_names

    def test_langgraph_dep_present(self, bom_with_deps: dict[str, Any]) -> None:
        dep_names = {c["name"] for c in bom_with_deps["components"] if c.get("purl")}
        assert "langgraph" in dep_names

    def test_pydantic_dep_present(self, bom_with_deps: dict[str, Any]) -> None:
        dep_names = {c["name"] for c in bom_with_deps["components"] if c.get("purl")}
        assert "pydantic" in dep_names

    def test_no_deps_when_empty_list_passed(self) -> None:
        """Passing deps=[] explicitly suppresses all dep components."""
        bom = _cdx("customer_service_bot", deps=[])
        dep_comps = [c for c in bom["components"] if c.get("purl")]
        assert dep_comps == [], "No dep components expected when deps=[]"

    def test_auto_deps_when_none_passed(self) -> None:
        """When deps=None, doc.deps from the automatic manifest scan are used."""
        bom = _cdx("customer_service_bot", deps=None)
        dep_comps = [c for c in bom["components"] if c.get("purl")]
        assert dep_comps, "Auto-scanned doc.deps should appear in CycloneDX when deps=None"


# ---------------------------------------------------------------------------
# Dependency edges (CycloneDX dependencies section)
# ---------------------------------------------------------------------------


class TestDependencyEdges:
    @pytest.fixture(scope="class")
    def bom(self) -> dict[str, Any]:
        return _cdx("customer_service_bot")

    def test_edge_refs_are_valid_bom_refs(self, bom: dict[str, Any]) -> None:
        all_refs = {c["bom-ref"] for c in bom["components"]}
        for dep in bom["dependencies"]:
            assert dep["ref"] in all_refs, f"Edge ref {dep['ref']!r} not in component bom-refs"
            for target in dep["dependsOn"]:
                assert target in all_refs, f"Edge target {target!r} not in component bom-refs"

    def test_no_self_referential_edges(self, bom: dict[str, Any]) -> None:
        for dep in bom["dependencies"]:
            assert dep["ref"] not in dep["dependsOn"], f"Self-referential edge: {dep['ref']!r}"


# ---------------------------------------------------------------------------
# Cross-app: RAG pipeline and CrewAI crew
# ---------------------------------------------------------------------------


class TestRagPipelineCycloneDx:
    @pytest.fixture(scope="class")
    def bom(self) -> dict[str, Any]:
        return _cdx("rag_pipeline")

    def test_datastore_nodes_map_to_data_type(self, bom: dict[str, Any]) -> None:
        doc = _extract("rag_pipeline")
        ds_names = {n.name for n in doc.nodes if n.component_type == ComponentType.DATASTORE}
        data_comps = {c["name"] for c in bom["components"] if c["type"] == "data"}
        for name in ds_names:
            assert name in data_comps, f"DATASTORE {name!r} not mapped to 'data' type"

    def test_anthropic_model_has_external_ref(self, bom: dict[str, Any]) -> None:
        anthropic_comps = [
            c
            for c in bom["components"]
            if c["type"] == "machine-learning-model"
            and any(
                p["name"] == "xelo:provider" and p["value"] == "anthropic"
                for p in c.get("properties", [])
            )
        ]
        assert anthropic_comps, "Expected Anthropic ML model component"
        for comp in anthropic_comps:
            refs = comp.get("externalReferences", [])
            assert refs, f"Anthropic model {comp['name']!r} has no externalReferences"


class TestCodeReviewCrewCycloneDx:
    @pytest.fixture(scope="class")
    def bom(self) -> dict[str, Any]:
        return _cdx("code_review_crew")

    def test_both_framework_adapters_present(self, bom: dict[str, Any]) -> None:
        app_comps = [c for c in bom["components"] if c["type"] == "application"]
        adapter_props = set()
        for comp in app_comps:
            for p in comp.get("properties", []):
                if p["name"] == "xelo:adapter":
                    adapter_props.add(p["value"])
        assert "crewai" in adapter_props or "autogen" in adapter_props, (
            f"Expected crewai or autogen adapter in application components, got: {adapter_props}"
        )

    def test_multi_provider_models_in_bom(self, bom: dict[str, Any]) -> None:
        ml_comps = [c for c in bom["components"] if c["type"] == "machine-learning-model"]
        providers = set()
        for comp in ml_comps:
            for p in comp.get("properties", []):
                if p["name"] == "xelo:provider":
                    providers.add(p["value"])
        assert "anthropic" in providers or "openai" in providers, (
            f"Expected multi-provider models, got: {providers}"
        )


# ---------------------------------------------------------------------------
# Full pipeline: extract + scan deps + serialize
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """End-to-end: extract AI BOM + scan deps → combined CycloneDX output."""

    def test_combined_component_count(self) -> None:
        doc = _extract("rag_pipeline")
        deps = DependencyScanner().scan(_APPS / "rag_pipeline")
        bom = AiSbomSerializer.to_cyclonedx(doc, deps=deps)
        ai_count = len(doc.nodes)
        dep_count = len(deps)
        assert len(bom["components"]) == ai_count + dep_count, (
            f"Expected {ai_count} AI + {dep_count} dep = {ai_count + dep_count} components, "
            f"got {len(bom['components'])}"
        )

    def test_json_serializable(self) -> None:
        import json

        doc = _extract("customer_service_bot")
        deps = DependencyScanner().scan(_APPS / "customer_service_bot")
        json_str = AiSbomSerializer.dump_cyclonedx_json(doc, deps=deps)
        # Must be valid JSON
        parsed = json.loads(json_str)
        assert parsed["bomFormat"] == "CycloneDX"

    def test_spec_version_override(self) -> None:
        doc = _extract("research_assistant")
        bom = AiSbomSerializer.to_cyclonedx(doc, spec_version="1.5")
        assert bom["specVersion"] == "1.5"
