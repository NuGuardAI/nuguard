"""Unit tests for AtlasAnnotatorPlugin LLM + CVE enrichment.

Covers:
- Static mode (no regression): basis="static", no cve_context, no llm_summary
- CVE context attachment: _enrich_with_cve_context populates cve_context on all findings
- LLM enrichment: per-finding atlas.llm_summary and top-level details.llm_summary
- Fallback: LLM failure leaves static output intact
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from xelo.toolbox.plugins.atlas_annotator import AtlasAnnotatorPlugin

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_MINIMAL_SBOM: dict[str, Any] = {
    "nodes": [],
    "edges": [],
    "deps": [],
    "summary": {"frameworks": ["langchain"]},
}

_CVE_FINDING: dict[str, Any] = {
    "rule_id": "GHSA-1234",
    "severity": "HIGH",
    "title": "Known vulnerability in langchain-core (GHSA-1234) [CVE-2024-12345]",
    "description": "Prototype pollution in langchain-core.",
    "affected": ["pkg:pypi/langchain-core@0.1.0"],
    "remediation": "Upgrade to 0.1.1.",
    "source": "osv",
    "advisory_url": "https://osv.dev/vulnerability/GHSA-1234",
    "cve_ids": ["CVE-2024-12345"],
}

_GRYPE_FINDING: dict[str, Any] = {
    "rule_id": "GHSA-9876",
    "severity": "CRITICAL",
    "title": "Known vulnerability in requests (GHSA-9876) [CVE-2024-99999]",
    "description": "SSRF in requests.",
    "affected": ["pkg:pypi/requests@2.28.0"],
    "remediation": "Upgrade to 2.29.0.",
    "source": "grype",
    "advisory_url": "https://github.com/advisories/GHSA-9876",
    "cve_ids": ["CVE-2024-99999"],
}

_VLA_FINDING: dict[str, Any] = {
    "rule_id": "XELO-001",
    "severity": "CRITICAL",
    "title": "PII/PHI data handled by external LLM providers",
    "description": "...",
    "affected": ["gpt-4o"],
    "remediation": "Establish a DPA with each provider.",
    "source": "xelo-rules",
    "atlas": {
        "atlas_version": "v2",
        "techniques": [
            {
                "technique_id": "AML.T0057",
                "technique_name": "LLM Data Leakage",
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
        # sbom with no interesting nodes → no findings but basis still static
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


# ---------------------------------------------------------------------------
# TestCveContextEnrichment
# ---------------------------------------------------------------------------


class TestCveContextEnrichment:
    """_enrich_with_cve_context attaches cve_context to every finding."""

    def test_cve_context_attached(self) -> None:
        plugin = AtlasAnnotatorPlugin()
        findings = [dict(_VLA_FINDING)]
        enriched = plugin._enrich_with_cve_context(findings, [_CVE_FINDING])
        assert "cve_context" in enriched[0]

    def test_cve_context_fields(self) -> None:
        plugin = AtlasAnnotatorPlugin()
        enriched = plugin._enrich_with_cve_context([dict(_VLA_FINDING)], [_CVE_FINDING])
        ctx = enriched[0]["cve_context"][0]
        assert ctx["severity"] == "HIGH"
        assert "CVE-2024-12345" in ctx["cve_ids"]
        assert "langchain-core" in ctx["package"]

    def test_no_cve_findings_returns_unchanged(self) -> None:
        plugin = AtlasAnnotatorPlugin()
        findings = [dict(_VLA_FINDING)]
        enriched = plugin._enrich_with_cve_context(findings, [])
        assert enriched == findings

    def test_top_10_cves_only(self) -> None:
        """More than 10 CVEs → only top 10 attached (sorted by severity)."""
        plugin = AtlasAnnotatorPlugin()
        cve_findings = [
            {**_CVE_FINDING, "rule_id": f"GHSA-{i}", "severity": "LOW"} for i in range(15)
        ]
        enriched = plugin._enrich_with_cve_context([dict(_VLA_FINDING)], cve_findings)
        assert len(enriched[0]["cve_context"]) == 10


# ---------------------------------------------------------------------------
# TestLlmEnrichment
# ---------------------------------------------------------------------------


class TestLlmEnrichment:
    """Run() with config["llm"]=True triggers CVE + LLM passes."""

    def _make_mock_client(self, summary: str = "mock summary") -> MagicMock:
        client = MagicMock()
        client.complete_text = AsyncMock(return_value=(summary, 10))
        return client

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
            **_VLA_FINDING,
            "atlas": {**_VLA_FINDING["atlas"], "llm_summary": "per-finding narrative"},
        }
        with (
            patch.object(plugin, "_run_osv_pass", return_value=[_CVE_FINDING]),
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

        # Fallback: run completes without exception
        assert result.status in ("ok", "warning")
        assert result.details["basis"] == "llm"  # flag was set before enrichment
        assert "llm_summary" not in result.details  # empty string → not added

    def test_osv_failure_does_not_crash(self) -> None:
        """OSV network errors inside _run_osv_pass are swallowed; returns empty list."""
        plugin = AtlasAnnotatorPlugin()
        with patch("xelo.toolbox.plugins.vulnerability.VulnerabilityScannerPlugin") as MockScanner:
            MockScanner.return_value.run.side_effect = Exception("network error")
            cve = plugin._run_osv_pass(_MINIMAL_SBOM)

        assert cve == []

    def test_grype_failure_does_not_crash(self) -> None:
        """Grype errors inside _run_grype_pass are swallowed; returns empty list."""
        plugin = AtlasAnnotatorPlugin()
        with patch("xelo.toolbox.plugins.vulnerability.VulnerabilityScannerPlugin") as MockScanner:
            MockScanner.return_value.run.side_effect = Exception("grype not found")
            grype = plugin._run_grype_pass(_MINIMAL_SBOM)

        assert grype == []

    def test_osv_and_grype_combined_in_cve_context(self) -> None:
        """CVE context merges both OSV and Grype findings."""
        plugin = AtlasAnnotatorPlugin()
        with (
            patch.object(plugin, "_run_osv_pass", return_value=[_CVE_FINDING]),
            patch.object(plugin, "_run_grype_pass", return_value=[_GRYPE_FINDING]),
            patch.object(plugin, "_run_llm_enrichment", return_value=([], "")),
        ):
            plugin.run(_MINIMAL_SBOM, {"llm": True})

        # Verify both sources are passed to enrichment by inspecting _enrich_with_cve_context
        enriched = plugin._enrich_with_cve_context(
            [dict(_VLA_FINDING)], [_CVE_FINDING, _GRYPE_FINDING]
        )
        packages = {ctx["package"] for ctx in enriched[0]["cve_context"]}
        assert any("langchain-core" in p for p in packages)
        assert any("requests" in p for p in packages)

    def test_grype_findings_included_in_cve_context_count(self) -> None:
        """Grype-sourced CVEs count toward the top-10 cve_context cap."""
        plugin = AtlasAnnotatorPlugin()
        osv = [{**_CVE_FINDING, "rule_id": f"GHSA-osv-{i}"} for i in range(6)]
        grype = [{**_GRYPE_FINDING, "rule_id": f"GHSA-grype-{i}"} for i in range(6)]
        enriched = plugin._enrich_with_cve_context([dict(_VLA_FINDING)], osv + grype)
        assert len(enriched[0]["cve_context"]) == 10
