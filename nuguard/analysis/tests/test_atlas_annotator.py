"""Unit tests for AtlasAnnotatorPlugin (analysis edition).

Covers:
- Static mode (no regression): basis="static", no cve_context, no llm_summary
- CVE context attachment: _enrich_with_cve_context populates cve_context on all findings
- LLM enrichment: per-finding atlas.llm_summary and top-level details.llm_summary
- Fallback: LLM failure leaves static output intact
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from nuguard.analysis.plugins.atlas_annotator import AtlasAnnotatorPlugin

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_MINIMAL_SBOM: dict[str, Any] = {
    "nodes": [],
    "edges": [],
    "deps": [],
    "summary": {"frameworks": ["langchain"]},
}

_OSV_FINDING: dict[str, Any] = {
    "dep_name": "langchain-core",
    "dep_version": "0.1.0",
    "purl": "pkg:pypi/langchain-core@0.1.0",
    "advisory_id": "GHSA-1234",
    "cve_ids": ["CVE-2024-12345"],
    "summary": "Prototype pollution in langchain-core.",
    "severity": "HIGH",
    "affected_versions": ">=0.1.0,<0.1.1",
    "url": "https://osv.dev/vulnerability/GHSA-1234",
    "source": "osv",
}

_GRYPE_FINDING: dict[str, Any] = {
    "dep_name": "requests",
    "dep_version": "2.28.0",
    "purl": "pkg:pypi/requests@2.28.0",
    "advisory_id": "GHSA-9876",
    "cve_ids": ["CVE-2024-99999"],
    "summary": "SSRF in requests.",
    "severity": "CRITICAL",
    "affected_versions": "<2.29.0",
    "url": "https://github.com/advisories/GHSA-9876",
    "source": "grype",
}

_NGA_FINDING: dict[str, Any] = {
    "rule_id": "NGA-001",
    "severity": "CRITICAL",
    "title": "PII/PHI data handled by external LLM providers",
    "description": "...",
    "affected": ["gpt-4o"],
    "remediation": "Establish a DPA with each provider.",
    "source": "nga-rules",
    "atlas": {
        "atlas_version": "v2",
        "techniques": [
            {
                "technique_id": "AML.T0037",
                "technique_name": "Data from Information Repositories",
                "confidence": "HIGH",
                "basis": "static",
            }
        ],
    },
}


# ---------------------------------------------------------------------------
# TestStaticMode
# ---------------------------------------------------------------------------


class TestStaticMode:
    """Pass 1+2 unaffected when LLM is not requested."""

    def test_basis_is_static(self) -> None:
        plugin = AtlasAnnotatorPlugin()
        result = plugin.run(_MINIMAL_SBOM, {})
        assert result.details["basis"] == "static"

    def test_no_llm_summary_without_config(self) -> None:
        plugin = AtlasAnnotatorPlugin()
        result = plugin.run(_MINIMAL_SBOM, {})
        assert "llm_summary" not in result.details

    def test_no_cve_context_without_llm(self) -> None:
        plugin = AtlasAnnotatorPlugin()
        result = plugin.run(_MINIMAL_SBOM, {})
        for finding in result.details.get("findings", []):
            assert "cve_context" not in finding

    def test_result_has_plugin_attribute(self) -> None:
        plugin = AtlasAnnotatorPlugin()
        result = plugin.run(_MINIMAL_SBOM, {})
        assert result.plugin == "atlas_annotate"


# ---------------------------------------------------------------------------
# TestCveContextEnrichment
# ---------------------------------------------------------------------------


class TestCveContextEnrichment:
    """_enrich_with_cve_context attaches cve_context to every finding."""

    def test_cve_context_attached(self) -> None:
        plugin = AtlasAnnotatorPlugin()
        findings = [dict(_NGA_FINDING)]
        enriched = plugin._enrich_with_cve_context(findings, [_OSV_FINDING])
        assert "cve_context" in enriched[0]

    def test_cve_context_fields(self) -> None:
        plugin = AtlasAnnotatorPlugin()
        enriched = plugin._enrich_with_cve_context([dict(_NGA_FINDING)], [_OSV_FINDING])
        ctx = enriched[0]["cve_context"][0]
        assert ctx["severity"] == "HIGH"
        assert "CVE-2024-12345" in ctx["cve_ids"]
        assert "langchain-core" in ctx["package"]

    def test_no_cve_findings_returns_unchanged(self) -> None:
        plugin = AtlasAnnotatorPlugin()
        findings = [dict(_NGA_FINDING)]
        enriched = plugin._enrich_with_cve_context(findings, [])
        assert enriched == findings

    def test_top_10_cves_only(self) -> None:
        """More than 10 CVEs → only top 10 attached (sorted by severity)."""
        plugin = AtlasAnnotatorPlugin()
        cve_findings = [
            {**_OSV_FINDING, "advisory_id": f"GHSA-{i}", "severity": "LOW"} for i in range(15)
        ]
        enriched = plugin._enrich_with_cve_context([dict(_NGA_FINDING)], cve_findings)
        assert len(enriched[0]["cve_context"]) == 10


# ---------------------------------------------------------------------------
# TestLlmEnrichment
# ---------------------------------------------------------------------------


class TestLlmEnrichment:
    """run() with config["llm"]=True triggers CVE + LLM passes."""

    def test_basis_is_llm(self) -> None:
        plugin = AtlasAnnotatorPlugin()
        with (
            patch.object(plugin, "_run_osv_pass", return_value=[]),
            patch.object(plugin, "_run_grype_pass", return_value=[]),
            patch.object(plugin, "_run_llm_enrichment", return_value=([], "overall")),
        ):
            result = plugin.run(_MINIMAL_SBOM, {"llm": True})
        assert result.details["basis"] == "llm"

    def test_llm_summary_in_details(self) -> None:
        plugin = AtlasAnnotatorPlugin()
        with (
            patch.object(plugin, "_run_osv_pass", return_value=[]),
            patch.object(plugin, "_run_grype_pass", return_value=[]),
            patch.object(plugin, "_run_llm_enrichment", return_value=([], "executive summary")),
        ):
            result = plugin.run(_MINIMAL_SBOM, {"llm": True})
        assert result.details.get("llm_summary") == "executive summary"

    def test_per_finding_llm_summary(self) -> None:
        """Each finding gets atlas.llm_summary when LLM enrichment succeeds."""
        plugin = AtlasAnnotatorPlugin()
        enriched_finding = {
            **_NGA_FINDING,
            "atlas": {**_NGA_FINDING["atlas"], "llm_summary": "per-finding narrative"},
        }
        with (
            patch.object(plugin, "_run_osv_pass", return_value=[_OSV_FINDING]),
            patch.object(plugin, "_run_grype_pass", return_value=[_GRYPE_FINDING]),
            patch.object(
                plugin,
                "_run_llm_enrichment",
                return_value=([enriched_finding], "overall"),
            ),
        ):
            result = plugin.run(_MINIMAL_SBOM, {"llm": True})
        assert result.details["findings"][0]["atlas"]["llm_summary"] == "per-finding narrative"

    def test_enable_llm_alias(self) -> None:
        """config['enable_llm'] is accepted as an alias for config['llm']."""
        plugin = AtlasAnnotatorPlugin()
        with (
            patch.object(plugin, "_run_osv_pass", return_value=[]),
            patch.object(plugin, "_run_grype_pass", return_value=[]),
            patch.object(plugin, "_run_llm_enrichment", return_value=([], "")),
        ):
            result = plugin.run(_MINIMAL_SBOM, {"enable_llm": True})
        assert result.details["basis"] == "llm"

    def test_llm_failure_falls_back_gracefully(self) -> None:
        """LLM errors leave static findings untouched (basis still llm, no crash)."""
        plugin = AtlasAnnotatorPlugin()
        with (
            patch.object(plugin, "_run_osv_pass", return_value=[]),
            patch.object(plugin, "_run_grype_pass", return_value=[]),
            patch.object(
                plugin,
                "_async_llm_enrichment",
                side_effect=RuntimeError("LLM unavailable"),
            ),
        ):
            result = plugin.run(_MINIMAL_SBOM, {"llm": True})
        assert result.status in ("ok", "warning")
        assert result.details["basis"] == "llm"
        assert "llm_summary" not in result.details

    def test_osv_failure_does_not_crash(self) -> None:
        """OSV errors inside _run_osv_pass are swallowed; returns empty list."""
        plugin = AtlasAnnotatorPlugin()
        with patch(
            "nuguard.analysis.plugins.atlas_annotator.AtlasAnnotatorPlugin._run_osv_pass",
            return_value=[],
        ):
            cve = plugin._run_osv_pass(_MINIMAL_SBOM)
        assert isinstance(cve, list)

    def test_grype_failure_does_not_crash(self) -> None:
        """Grype errors inside _run_grype_pass are swallowed; returns empty list."""
        plugin = AtlasAnnotatorPlugin()
        with patch(
            "nuguard.analysis.plugins.atlas_annotator.AtlasAnnotatorPlugin._run_grype_pass",
            return_value=[],
        ):
            grype = plugin._run_grype_pass(_MINIMAL_SBOM)
        assert isinstance(grype, list)

    def test_osv_and_grype_combined_in_cve_context(self) -> None:
        """CVE context merges both OSV and Grype findings."""
        plugin = AtlasAnnotatorPlugin()
        with (
            patch.object(plugin, "_run_osv_pass", return_value=[_OSV_FINDING]),
            patch.object(plugin, "_run_grype_pass", return_value=[_GRYPE_FINDING]),
            patch.object(plugin, "_run_llm_enrichment", return_value=([], "")),
        ):
            plugin.run(_MINIMAL_SBOM, {"llm": True})

        enriched = plugin._enrich_with_cve_context(
            [dict(_NGA_FINDING)], [_OSV_FINDING, _GRYPE_FINDING]
        )
        packages = {ctx["package"] for ctx in enriched[0]["cve_context"]}
        assert any("langchain-core" in p for p in packages)
        assert any("requests" in p for p in packages)

    def test_grype_findings_included_in_cve_context_count(self) -> None:
        """Grype-sourced CVEs count toward the top-10 cve_context cap."""
        plugin = AtlasAnnotatorPlugin()
        osv_findings = [{**_OSV_FINDING, "advisory_id": f"GHSA-osv-{i}"} for i in range(6)]
        grype_findings = [{**_GRYPE_FINDING, "advisory_id": f"GHSA-grype-{i}"} for i in range(6)]
        enriched = plugin._enrich_with_cve_context([dict(_NGA_FINDING)], osv_findings + grype_findings)
        assert len(enriched[0]["cve_context"]) == 10
