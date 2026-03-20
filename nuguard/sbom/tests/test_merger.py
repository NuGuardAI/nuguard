"""Tests for AiBomMerger and CycloneDxGenerator.

Validates the full two-phase pipeline from the reference architecture:

  Phase 1 — Standard SBOM: cyclonedx-py CLI (or dep-scanner fallback)
  Phase 2 — AI-BOM:        Xelo AST extractors
  Merge   — Normalization: unified CycloneDX 1.6 BOM with aibom:* enrichment
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xelo.cdx_tools import CycloneDxGenerator
from xelo.config import AiSbomConfig
from xelo.extractor import AiSbomExtractor
from xelo.merger import AiBomMerger, _infer_tool_risk, _normalise_name, _prompt_hash
from xelo.models import AiSbomDocument

_APPS = Path(__file__).parent / "fixtures" / "apps"
_PY_ONLY = AiSbomConfig(include_extensions={".py"}, enable_llm=False)


def _extract(app: str) -> AiSbomDocument:
    return AiSbomExtractor().extract_from_path(_APPS / app, _PY_ONLY)


def _minimal_cdx_bom(components: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a minimal CycloneDX BOM dict for testing."""
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "serialNumber": "urn:uuid:test-0000",
        "metadata": {
            "timestamp": "2026-01-01T00:00:00Z",
            "tools": [{"vendor": "Syft", "name": "syft", "version": "1.0.0"}],
            "component": {"type": "application", "name": "test-app"},
        },
        "components": components or [],
        "dependencies": [],
    }


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_normalise_name_hyphen_collapse(self) -> None:
        assert _normalise_name("langchain_openai") == "langchain-openai"

    def test_normalise_name_case(self) -> None:
        assert _normalise_name("OpenAI") == "openai"

    def test_infer_tool_risk_filesystem(self) -> None:
        assert _infer_tool_risk("write_file") == "filesystem"

    def test_infer_tool_risk_network(self) -> None:
        assert _infer_tool_risk("http_get") == "network"

    def test_infer_tool_risk_general_fallback(self) -> None:
        assert _infer_tool_risk("calculate_tax") == "general"

    def test_prompt_hash_is_sha256_prefix(self) -> None:
        h = _prompt_hash("You are a helpful assistant.")
        assert h.startswith("sha256:")
        assert len(h) == len("sha256:") + 16  # 16-char truncated hex


# ---------------------------------------------------------------------------
# Merger: basic structure
# ---------------------------------------------------------------------------


class TestMergerStructure:
    @pytest.fixture(scope="class")
    def merged(self) -> dict[str, Any]:
        doc = _extract("customer_service_bot")
        std = _minimal_cdx_bom()
        return AiBomMerger().merge(std, doc, generator_method="test")

    def test_bom_format_preserved(self, merged: dict[str, Any]) -> None:
        assert merged["bomFormat"] == "CycloneDX"

    def test_spec_version_preserved(self, merged: dict[str, Any]) -> None:
        assert merged["specVersion"] == "1.6"

    def test_serial_number_preserved(self, merged: dict[str, Any]) -> None:
        assert merged["serialNumber"] == "urn:uuid:test-0000"

    def test_vela_tool_added(self, merged: dict[str, Any]) -> None:
        tools = merged["metadata"]["tools"]
        assert any(t.get("name") == "xelo" for t in tools)

    def test_original_tool_preserved(self, merged: dict[str, Any]) -> None:
        tools = merged["metadata"]["tools"]
        assert any(t.get("name") == "syft" for t in tools)

    def test_ai_components_added(self, merged: dict[str, Any]) -> None:
        assert len(merged["components"]) > 0

    def test_dependencies_list_present(self, merged: dict[str, Any]) -> None:
        assert "dependencies" in merged
        assert isinstance(merged["dependencies"], list)

    def test_metadata_properties_present(self, merged: dict[str, Any]) -> None:
        props = merged["metadata"].get("properties", [])
        names = {p["name"] for p in props}
        assert "aibom:version" in names
        assert "aibom:generator" in names
        assert "aibom:scanTarget" in names
        assert "aibom:aiComponentTotal" in names

    def test_quality_gate_property(self, merged: dict[str, Any]) -> None:
        props = {p["name"]: p["value"] for p in merged["metadata"].get("properties", [])}
        assert "aibom:qualityGate" in props
        assert props["aibom:qualityGate"] in {"pass", "warn"}

    def test_confidence_summary_in_metadata(self, merged: dict[str, Any]) -> None:
        props = {p["name"] for p in merged["metadata"].get("properties", [])}
        assert "aibom:avgConfidence" in props
        assert "aibom:minConfidence" in props


# ---------------------------------------------------------------------------
# Merger: aibom:* properties on AI components
# ---------------------------------------------------------------------------


class TestAibomProperties:
    @pytest.fixture(scope="class")
    def merged(self) -> dict[str, Any]:
        doc = _extract("customer_service_bot")
        std = _minimal_cdx_bom()
        return AiBomMerger().merge(std, doc)

    def test_all_ai_components_have_component_type_prop(self, merged: dict[str, Any]) -> None:
        for comp in merged["components"]:
            prop_names = {p["name"] for p in comp.get("properties", [])}
            if any(p["name"].startswith("aibom:") for p in comp.get("properties", [])):
                assert "aibom:componentType" in prop_names, (
                    f"Component {comp['name']!r} missing aibom:componentType"
                )

    def test_model_components_have_provider_prop(self, merged: dict[str, Any]) -> None:
        ml_comps = [c for c in merged["components"] if c["type"] == "machine-learning-model"]
        enriched = [
            c
            for c in ml_comps
            if any(p["name"] == "aibom:provider" for p in c.get("properties", []))
        ]
        assert enriched, "Expected at least one ML model with aibom:provider"

    def test_model_card_url_in_properties(self, merged: dict[str, Any]) -> None:
        ml_comps = [c for c in merged["components"] if c["type"] == "machine-learning-model"]
        url_props = [
            c
            for c in ml_comps
            if any(p["name"] == "aibom:modelCardUrl" for p in c.get("properties", []))
        ]
        assert url_props, "Expected at least one ML model with aibom:modelCardUrl"

    def test_model_family_in_properties(self, merged: dict[str, Any]) -> None:
        ml_comps = [c for c in merged["components"] if c["type"] == "machine-learning-model"]
        family_props = [
            c
            for c in ml_comps
            if any(p["name"] == "aibom:modelFamily" for p in c.get("properties", []))
        ]
        assert family_props, "Expected at least one ML model with aibom:modelFamily"

    def test_agent_framework_prop_on_agents(self, merged: dict[str, Any]) -> None:
        app_comps = [c for c in merged["components"] if c["type"] == "application"]
        fw_props = [
            c
            for c in app_comps
            if any(p["name"] == "aibom:agentFramework" for p in c.get("properties", []))
        ]
        assert fw_props, "Expected agents/frameworks with aibom:agentFramework property"

    def test_tool_risk_category_on_tools(self, merged: dict[str, Any]) -> None:
        lib_comps = [c for c in merged["components"] if c["type"] == "library"]
        tool_comps = [
            c
            for c in lib_comps
            if any(
                p["name"] == "aibom:componentType" and p["value"] == "TOOL"
                for p in c.get("properties", [])
            )
        ]
        for comp in tool_comps:
            prop_names = {p["name"] for p in comp.get("properties", [])}
            assert "aibom:toolRiskCategory" in prop_names, (
                f"TOOL {comp['name']!r} missing aibom:toolRiskCategory"
            )

    def test_prompt_hash_on_prompts(self) -> None:
        doc = _extract("research_assistant")
        std = _minimal_cdx_bom()
        merged = AiBomMerger().merge(std, doc)
        prompt_comps = [
            c
            for c in merged["components"]
            if any(
                p["name"] == "aibom:componentType" and p["value"] == "PROMPT"
                for p in c.get("properties", [])
            )
            and any(p["name"] == "aibom:promptHash" for p in c.get("properties", []))
        ]
        assert prompt_comps, "Expected enriched PROMPT with aibom:promptHash"


# ---------------------------------------------------------------------------
# Merger: deduplication (dep component enrichment)
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_existing_dep_enriched_not_duplicated(self) -> None:
        """langgraph as both a dep component and a FRAMEWORK node → one component."""
        doc = _extract("customer_service_bot")
        std = _minimal_cdx_bom(
            [
                {
                    "bom-ref": "pkg:pypi/langgraph",
                    "type": "library",
                    "name": "langgraph",
                    "purl": "pkg:pypi/langgraph",
                    "version": "0.2.0",
                    "properties": [],
                }
            ]
        )
        merged = AiBomMerger().merge(std, doc)
        # Only one langgraph component
        langgraph_comps = [c for c in merged["components"] if c["name"] == "langgraph"]
        assert len(langgraph_comps) == 1, (
            f"Expected 1 langgraph component, got {len(langgraph_comps)}"
        )

    def test_enriched_dep_has_aibom_properties(self) -> None:
        """The enriched dep gets aibom:* properties from the AI adapter."""
        doc = _extract("customer_service_bot")
        std = _minimal_cdx_bom(
            [
                {
                    "bom-ref": "pkg:pypi/langgraph",
                    "type": "library",
                    "name": "langgraph",
                    "purl": "pkg:pypi/langgraph",
                    "properties": [],
                }
            ]
        )
        merged = AiBomMerger().merge(std, doc)
        langgraph = next(c for c in merged["components"] if c["name"] == "langgraph")
        aibom_props = {
            p["name"] for p in langgraph.get("properties", []) if p["name"].startswith("aibom:")
        }
        assert aibom_props, "Expected aibom:* properties on enriched langgraph component"

    def test_enriched_type_upgraded_to_application(self) -> None:
        """A library dep that is also a FRAMEWORK gets upgraded to 'application'."""
        doc = _extract("customer_service_bot")
        # Make langgraph a plain library initially
        std = _minimal_cdx_bom(
            [
                {
                    "bom-ref": "pkg:pypi/langgraph",
                    "type": "library",
                    "name": "langgraph",
                    "purl": "pkg:pypi/langgraph",
                    "properties": [],
                }
            ]
        )
        merged = AiBomMerger().merge(std, doc)
        langgraph = next(c for c in merged["components"] if c["name"] == "langgraph")
        assert langgraph["type"] == "application", (
            f"Expected langgraph type upgraded to 'application', got {langgraph['type']!r}"
        )

    def test_unknown_ai_component_added_as_new(self) -> None:
        """AI nodes with no matching dep appear as new components."""
        doc = _extract("customer_service_bot")
        # Empty standard BOM
        std = _minimal_cdx_bom([])
        merged = AiBomMerger().merge(std, doc)
        assert len(merged["components"]) == len(doc.nodes), (
            f"Expected all AI nodes as new components; "
            f"got {len(merged['components'])} vs {len(doc.nodes)} nodes"
        )

    def test_no_duplicate_bom_refs(self) -> None:
        """All bom-ref values in the merged BOM are unique."""
        doc = _extract("customer_service_bot")
        std = _minimal_cdx_bom()
        merged = AiBomMerger().merge(std, doc)
        refs = [c.get("bom-ref", "") for c in merged["components"]]
        refs = [r for r in refs if r]
        assert len(refs) == len(set(refs)), f"Duplicate bom-refs: {refs}"


# ---------------------------------------------------------------------------
# Merger: relationship edges
# ---------------------------------------------------------------------------


class TestMergedEdges:
    @pytest.fixture(scope="class")
    def merged(self) -> dict[str, Any]:
        doc = _extract("customer_service_bot")
        std = _minimal_cdx_bom()
        return AiBomMerger().merge(std, doc)

    def test_edges_in_dependencies(self, merged: dict[str, Any]) -> None:
        """AI relationship edges appear in the CycloneDX dependencies section."""
        deps = merged["dependencies"]
        assert deps, "Expected dependency entries from AI edges"

    def test_edge_refs_valid(self, merged: dict[str, Any]) -> None:
        """All edge refs point to existing bom-refs."""
        all_refs = {c.get("bom-ref") for c in merged["components"]}
        for dep in merged["dependencies"]:
            assert dep["ref"] in all_refs, f"dep ref {dep['ref']!r} not in components"
            for target in dep.get("dependsOn", []):
                assert target in all_refs, f"dep target {target!r} not in components"


# ---------------------------------------------------------------------------
# CycloneDxGenerator: fallback
# ---------------------------------------------------------------------------


class TestCycloneDxGeneratorFallback:
    """Test the dep-scanner fallback (always available, no CLI needed)."""

    def test_fallback_produces_valid_bom(self) -> None:
        gen = CycloneDxGenerator()
        # Temporarily hide cyclonedx-py by monkey-patching
        import xelo.cdx_tools as cdx_mod

        orig = cdx_mod._cdx_py_available
        cdx_mod._cdx_py_available = lambda: False  # type: ignore[attr-defined]
        try:
            bom, method = gen.generate(_APPS / "customer_service_bot")
        finally:
            cdx_mod._cdx_py_available = orig  # type: ignore[attr-defined]

        assert bom["bomFormat"] == "CycloneDX"
        assert method == "dep-scanner"

    def test_fallback_includes_deps(self) -> None:
        import xelo.cdx_tools as cdx_mod

        orig = cdx_mod._cdx_py_available
        cdx_mod._cdx_py_available = lambda: False  # type: ignore[attr-defined]
        try:
            bom, _ = CycloneDxGenerator().generate(_APPS / "customer_service_bot")
        finally:
            cdx_mod._cdx_py_available = orig  # type: ignore[attr-defined]

        names = {c["name"] for c in bom["components"]}
        assert "langgraph" in names
        assert "pydantic" in names

    def test_fallback_has_cdx_note_property(self) -> None:
        import xelo.cdx_tools as cdx_mod

        orig = cdx_mod._cdx_py_available
        cdx_mod._cdx_py_available = lambda: False  # type: ignore[attr-defined]
        try:
            bom, _ = CycloneDxGenerator().generate(_APPS / "customer_service_bot")
        finally:
            cdx_mod._cdx_py_available = orig  # type: ignore[attr-defined]

        props = bom.get("metadata", {}).get("properties", [])
        generators = [p["value"] for p in props if p["name"] == "cdx:generator"]
        assert "xelo-dep-scanner" in generators


# ---------------------------------------------------------------------------
# CycloneDxGenerator: cyclonedx-py CLI (if available)
# ---------------------------------------------------------------------------


class TestCycloneDxGeneratorCli:
    """Tests that exercise the cyclonedx-py CLI path (skipped if not installed)."""

    @pytest.fixture(scope="class")
    def cdx_available(self) -> bool:
        from xelo.cdx_tools import _cdx_py_available

        return _cdx_py_available()

    def test_cli_generates_valid_bom_for_requirements(self, cdx_available: bool) -> None:
        if not cdx_available:
            pytest.skip("cyclonedx-py not installed")
        gen = CycloneDxGenerator()
        bom, method = gen.generate(_APPS / "research_assistant")
        assert bom["bomFormat"] == "CycloneDX"
        assert "cyclonedx-py" in method

    def test_cli_bom_has_components(self, cdx_available: bool) -> None:
        if not cdx_available:
            pytest.skip("cyclonedx-py not installed")
        bom, _ = CycloneDxGenerator().generate(_APPS / "research_assistant")
        assert bom["components"], "Expected at least one dep component from cyclonedx-py"

    def test_cli_components_have_purls(self, cdx_available: bool) -> None:
        if not cdx_available:
            pytest.skip("cyclonedx-py not installed")
        bom, _ = CycloneDxGenerator().generate(_APPS / "research_assistant")
        comps_with_purl = [c for c in bom["components"] if c.get("purl")]
        assert comps_with_purl, "Expected components with PURL from cyclonedx-py"

    def test_cli_poetry_project(self, cdx_available: bool) -> None:
        if not cdx_available:
            pytest.skip("cyclonedx-py not installed")
        bom, method = CycloneDxGenerator().generate(_APPS / "code_review_crew")
        assert bom["bomFormat"] == "CycloneDX"
        # Poetry lock is not present in the fixture — should fall back to dep-scanner
        assert method in {"cyclonedx-py/poetry", "dep-scanner"}


# ---------------------------------------------------------------------------
# Full end-to-end pipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """Phase 1 (standard BOM) + Phase 2 (AI extraction) + merge."""

    def test_unified_bom_has_both_dep_and_ai_components(self) -> None:
        import xelo.cdx_tools as cdx_mod

        orig = cdx_mod._cdx_py_available
        cdx_mod._cdx_py_available = lambda: False  # type: ignore[attr-defined]
        try:
            gen = CycloneDxGenerator()
            bom, method = gen.generate(_APPS / "customer_service_bot")
        finally:
            cdx_mod._cdx_py_available = orig  # type: ignore[attr-defined]

        doc = _extract("customer_service_bot")
        unified = AiBomMerger().merge(bom, doc, generator_method=method)

        dep_comps = [c for c in unified["components"] if c.get("purl", "").startswith("pkg:pypi/")]
        ai_comps = [
            c
            for c in unified["components"]
            if any(p["name"] == "aibom:componentType" for p in c.get("properties", []))
        ]
        assert dep_comps, "Expected dep components in unified BOM"
        assert ai_comps, "Expected AI components in unified BOM"

    def test_unified_bom_json_serializable(self) -> None:
        import xelo.cdx_tools as cdx_mod

        orig = cdx_mod._cdx_py_available
        cdx_mod._cdx_py_available = lambda: False  # type: ignore[attr-defined]
        try:
            bom, method = CycloneDxGenerator().generate(_APPS / "rag_pipeline")
        finally:
            cdx_mod._cdx_py_available = orig  # type: ignore[attr-defined]

        doc = _extract("rag_pipeline")
        unified = AiBomMerger().merge(bom, doc, generator_method=method)
        # Must be JSON-serializable
        serialized = json.dumps(unified)
        reparsed = json.loads(serialized)
        assert reparsed["bomFormat"] == "CycloneDX"

    def test_metadata_generator_method_recorded(self) -> None:
        bom = _minimal_cdx_bom()
        doc = _extract("research_assistant")
        unified = AiBomMerger().merge(bom, doc, generator_method="cyclonedx-py/requirements")
        props = {p["name"]: p["value"] for p in unified["metadata"].get("properties", [])}
        assert props.get("aibom:depsBomMethod") == "cyclonedx-py/requirements"

    def test_merged_bom_quality_gate_passes_for_real_apps(self) -> None:
        for app in ("customer_service_bot", "research_assistant", "rag_pipeline"):
            doc = _extract(app)
            unified = AiBomMerger().merge(_minimal_cdx_bom(), doc)
            props = {p["name"]: p["value"] for p in unified["metadata"].get("properties", [])}
            assert props.get("aibom:qualityGate") == "pass", (
                f"Quality gate failed for {app}: {props}"
            )

    def test_count_properties_match_actual_nodes(self) -> None:
        doc = _extract("customer_service_bot")
        unified = AiBomMerger().merge(_minimal_cdx_bom(), doc)
        props = {p["name"]: p["value"] for p in unified["metadata"].get("properties", [])}
        assert int(props["aibom:aiComponentTotal"]) == len(doc.nodes)
        assert int(props["aibom:aiRelationships"]) == len(doc.edges)
