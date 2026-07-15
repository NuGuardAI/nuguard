"""Tests for nuguard.analysis.public_api — the Pydantic-only entry point for
callers outside the CLI.

Focuses on: (1) JSON round-tripping of the request/result models, (2) that
run_analysis() constructs StaticAnalyzer with exactly the request's fields
(converting source_path back to a Path) and reads back the analyzer's
instance attributes into AnalysisRunResult, proving no functionality is lost
relative to the CLI's direct StaticAnalyzer usage, and (3) that remediation
synthesis is best-effort while analyze() failures are not.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from nuguard.analysis.public_api import (
    AnalysisRunRequest,
    AnalysisRunResult,
    run_analysis,
)
from nuguard.models.finding import Finding, Severity
from nuguard.models.token_usage import TokenUsage


def _finding(affected_component: str = "next@15.2.4") -> Finding:
    return Finding(
        finding_id="osv-GHSA-1",
        title="t",
        severity=Severity.HIGH,
        description="d",
        affected_component=affected_component,
        remediation="upgrade to 15.2.5",
    )


def _make_mock_analyzer(findings: list[Finding]) -> MagicMock:
    instance = MagicMock()
    instance.analyze.return_value = findings
    instance.tool_status = {"osv": {"status": "ok", "findings": str(len(findings))}}
    instance.nga_audit = [{"rule_id": "NGA-001", "status": "PASS"}]
    instance.sc_audit = [{"rule_id": "NGA-SC-001", "status": "PASS"}]
    instance.token_usage = TokenUsage(input_tokens=5, output_tokens=7)
    return instance


# ---------------------------------------------------------------------------
# JSON round-trips
# ---------------------------------------------------------------------------


def test_analysis_run_request_json_roundtrip():
    req = AnalysisRunRequest(
        source_path="/tmp/app",
        min_severity=Severity.HIGH,
        supply_chain_threat_intel_feeds=["feed1"],
    )
    restored = AnalysisRunRequest.model_validate_json(req.model_dump_json())
    assert restored.source_path == "/tmp/app"
    assert restored.min_severity == Severity.HIGH
    assert restored.supply_chain_threat_intel_feeds == ["feed1"]


def test_analysis_run_request_defaults_match_static_analyzer_constructor():
    req = AnalysisRunRequest()
    assert req.enable_atlas is True
    assert req.enable_osv is True
    assert req.enable_grype is True
    assert req.enable_checkov is True
    assert req.enable_trivy is True
    assert req.enable_semgrep is True
    assert req.enable_supply_chain is True
    assert req.source_path is None
    assert req.min_severity == Severity.LOW
    assert req.verbose is False
    assert req.grype_timeout == 180.0
    assert req.grype_retries == 3
    assert req.supply_chain_profile == "standard"
    assert req.supply_chain_verify_artifacts == "off"


def test_analysis_run_request_rejects_invalid_min_severity():
    with pytest.raises(ValidationError):
        AnalysisRunRequest(min_severity="bogus")


def test_analysis_run_result_json_roundtrip():
    result = AnalysisRunResult(
        findings=[_finding()],
        tool_status={"osv": {"status": "ok", "findings": "1"}},
        nga_audit=[{"rule_id": "NGA-001"}],
        sc_audit=[{"rule_id": "NGA-SC-001"}],
        token_usage=TokenUsage(input_tokens=1, output_tokens=2),
    )
    restored = AnalysisRunResult.model_validate_json(result.model_dump_json())
    assert restored.findings[0].affected_component == "next@15.2.4"
    assert restored.tool_status["osv"]["status"] == "ok"
    assert restored.nga_audit == [{"rule_id": "NGA-001"}]
    assert restored.sc_audit == [{"rule_id": "NGA-SC-001"}]
    assert restored.token_usage.input_tokens == 1
    assert restored.remediation_plan == []


def test_analysis_run_result_defaults_are_empty():
    result = AnalysisRunResult(findings=[])
    assert result.tool_status == {}
    assert result.nga_audit == []
    assert result.sc_audit == []
    assert result.token_usage.input_tokens == 0
    assert result.remediation_plan == []


# ---------------------------------------------------------------------------
# run_analysis — thin-wrapper parity with StaticAnalyzer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_analysis_constructs_analyzer_and_builds_result():
    findings = [_finding()]
    mock_instance = _make_mock_analyzer(findings)
    sbom = MagicMock()

    with patch("nuguard.analysis.public_api.StaticAnalyzer") as mock_cls:
        mock_cls.return_value = mock_instance
        request = AnalysisRunRequest(min_severity=Severity.MEDIUM, source_path="/tmp/app")
        result = await run_analysis(request, sbom=sbom)

    mock_cls.assert_called_once()
    _, kwargs = mock_cls.call_args
    assert kwargs["min_severity"] == Severity.MEDIUM
    assert str(kwargs["source_path"]) == "/tmp/app"
    assert kwargs["enable_atlas"] is True

    mock_instance.analyze.assert_called_once_with(sbom)

    assert isinstance(result, AnalysisRunResult)
    assert len(result.findings) == 1
    assert result.tool_status == mock_instance.tool_status
    assert result.nga_audit == mock_instance.nga_audit
    assert result.sc_audit == mock_instance.sc_audit
    assert result.token_usage.input_tokens == 5


@pytest.mark.asyncio
async def test_run_analysis_source_path_none_stays_none():
    mock_instance = _make_mock_analyzer([])

    with patch("nuguard.analysis.public_api.StaticAnalyzer") as mock_cls:
        mock_cls.return_value = mock_instance
        request = AnalysisRunRequest()
        await run_analysis(request, sbom=MagicMock())

    _, kwargs = mock_cls.call_args
    assert kwargs["source_path"] is None


@pytest.mark.asyncio
async def test_run_analysis_passes_every_config_field():
    mock_instance = _make_mock_analyzer([])

    with patch("nuguard.analysis.public_api.StaticAnalyzer") as mock_cls:
        mock_cls.return_value = mock_instance
        request = AnalysisRunRequest(
            enable_osv=False,
            enable_grype=False,
            grype_timeout=30.0,
            grype_retries=1,
            supply_chain_profile="full",
            supply_chain_verify_artifacts="fail",
            atlas_config={"llm": True},
            verbose=True,
        )
        await run_analysis(request, sbom=MagicMock())

    _, kwargs = mock_cls.call_args
    assert kwargs["enable_osv"] is False
    assert kwargs["enable_grype"] is False
    assert kwargs["grype_timeout"] == 30.0
    assert kwargs["grype_retries"] == 1
    assert kwargs["supply_chain_profile"] == "full"
    assert kwargs["supply_chain_verify_artifacts"] == "fail"
    assert kwargs["atlas_config"] == {"llm": True}
    assert kwargs["verbose"] is True


@pytest.mark.asyncio
async def test_run_analysis_propagates_exceptions():
    """A full analysis run is not best-effort — real exceptions must not be swallowed."""
    mock_instance = MagicMock()
    mock_instance.analyze.side_effect = RuntimeError("sbom invalid")

    with patch("nuguard.analysis.public_api.StaticAnalyzer") as mock_cls:
        mock_cls.return_value = mock_instance
        request = AnalysisRunRequest()
        with pytest.raises(RuntimeError, match="sbom invalid"):
            await run_analysis(request, sbom=MagicMock())


# ---------------------------------------------------------------------------
# remediation_plan — structured, machine-actionable remediation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_analysis_populates_remediation_plan():
    """Findings + a real SBOM should produce structured RemediationArtefact objects,
    via the same RemediationSynthesizer behavior/redteam public APIs use."""
    from types import SimpleNamespace

    from nuguard.remediation.models import RemediationArtefact, RemediationArtefactType

    findings = [_finding()]
    mock_instance = _make_mock_analyzer(findings)
    fake_artefact = RemediationArtefact(
        finding_ids=["osv-GHSA-1"],
        component="unknown",
        component_type="TOOL",
        artefact_type=RemediationArtefactType.ARCHITECTURAL_CHANGE,
        priority="high",
        rationale="d",
    )

    with (
        patch("nuguard.analysis.public_api.StaticAnalyzer") as mock_cls,
        patch(
            "nuguard.remediation.synthesizer.RemediationSynthesizer.synthesize_findings_async"
        ) as mock_synth,
    ):
        mock_cls.return_value = mock_instance
        mock_synth.return_value = [fake_artefact]
        request = AnalysisRunRequest()
        result = await run_analysis(request, sbom=SimpleNamespace(nodes=[], edges=[]))

    mock_synth.assert_awaited_once()
    (finding_dicts,), _ = mock_synth.await_args
    assert finding_dicts[0]["finding_id"] == "osv-GHSA-1"
    assert finding_dicts[0]["affected_component"] == "next@15.2.4"
    assert result.remediation_plan == [fake_artefact]


@pytest.mark.asyncio
async def test_run_analysis_remediation_plan_empty_without_findings():
    mock_instance = _make_mock_analyzer([])

    with patch("nuguard.analysis.public_api.StaticAnalyzer") as mock_cls:
        mock_cls.return_value = mock_instance
        request = AnalysisRunRequest()
        result = await run_analysis(request, sbom=MagicMock())

    assert result.remediation_plan == []


@pytest.mark.asyncio
async def test_run_analysis_remediation_synthesis_failure_is_swallowed():
    """Remediation synthesis is best-effort — a failure must not fail the run."""
    from types import SimpleNamespace

    findings = [_finding()]
    mock_instance = _make_mock_analyzer(findings)

    with (
        patch("nuguard.analysis.public_api.StaticAnalyzer") as mock_cls,
        patch(
            "nuguard.remediation.synthesizer.RemediationSynthesizer.synthesize_findings_async",
            side_effect=RuntimeError("boom"),
        ),
    ):
        mock_cls.return_value = mock_instance
        request = AnalysisRunRequest()
        result = await run_analysis(request, sbom=SimpleNamespace(nodes=[], edges=[]))

    assert result.remediation_plan == []
    assert len(result.findings) == 1
