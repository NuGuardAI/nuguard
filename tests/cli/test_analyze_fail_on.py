"""Tests for ``nuguard analyze --fail-on`` exit-code behaviour.

The ``--fail-on`` flag controls the severity threshold at which the analyze
command exits with code 1.  Before this feature, the analyze command always
exited 1 when *any* finding was visible (filtered by ``--min-severity``),
regardless of its severity.  ``--fail-on`` aligns the analyze command with
the behaviour and redteam commands, which both respect ``output.fail_on``
from nuguard.yaml.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from nuguard.cli.main import app
from nuguard.models.finding import Finding, Severity

runner = CliRunner()

_FIXTURE_SBOM = (
    Path(__file__).parent.parent.parent
    / "nuguard"
    / "analysis"
    / "tests"
    / "fixtures"
    / "minimal.sbom.json"
)

# Minimal flags to skip external scanner invocations
_SKIP_SCANNERS = [
    "--no-atlas", "--no-osv", "--no-grype",
    "--no-checkov", "--no-trivy", "--no-semgrep",
    "--no-supply-chain",
]


def _make_finding(severity: str, title: str = "test finding") -> Finding:
    return Finding(
        finding_id=f"NGA-{severity.upper()}-001",
        severity=Severity(severity),
        title=title,
        description="test",
        affected_component="test",
        technique_id="NGA-001",
    )


def _mock_run_analysis(findings: list[Finding]):
    """Return an async mock for run_analysis that produces the given findings."""
    from nuguard.analysis.public_api import AnalysisRunResult

    async def _run(request, sbom=None, llm_client=None):  # noqa: ANN001
        return AnalysisRunResult(findings=findings)

    return AsyncMock(side_effect=_run)


class TestFailOnSeverity:
    """--fail-on controls the exit-code severity threshold."""

    def test_fail_on_critical_exits_zero_for_high_finding(self) -> None:
        """--fail-on critical should exit 0 when only high findings exist."""
        findings = [_make_finding("high")]
        with patch("nuguard.analysis.public_api.run_analysis", _mock_run_analysis(findings)):
            result = runner.invoke(
                app,
                ["analyze", "--sbom", str(_FIXTURE_SBOM), "--fail-on", "critical",
                 *_SKIP_SCANNERS],
            )
        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"

    def test_fail_on_high_exits_one_for_high_finding(self) -> None:
        """--fail-on high should exit 1 when a high finding exists."""
        findings = [_make_finding("high")]
        with patch("nuguard.analysis.public_api.run_analysis", _mock_run_analysis(findings)):
            result = runner.invoke(
                app,
                ["analyze", "--sbom", str(_FIXTURE_SBOM), "--fail-on", "high",
                 *_SKIP_SCANNERS],
            )
        assert result.exit_code == 1, f"Expected exit 1, got {result.exit_code}: {result.output}"

    def test_fail_on_medium_exits_zero_for_low_finding(self) -> None:
        """--fail-on medium should exit 0 when only low findings exist."""
        findings = [_make_finding("low")]
        with patch("nuguard.analysis.public_api.run_analysis", _mock_run_analysis(findings)):
            result = runner.invoke(
                app,
                ["analyze", "--sbom", str(_FIXTURE_SBOM), "--fail-on", "medium",
                 *_SKIP_SCANNERS],
            )
        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"

    def test_fail_on_low_exits_one_for_low_finding(self) -> None:
        """--fail-on low should exit 1 when a low finding exists (and is visible)."""
        findings = [_make_finding("low")]
        with patch("nuguard.analysis.public_api.run_analysis", _mock_run_analysis(findings)):
            result = runner.invoke(
                app,
                ["analyze", "--sbom", str(_FIXTURE_SBOM), "--fail-on", "low",
                 "--min-severity", "low", *_SKIP_SCANNERS],
            )
        assert result.exit_code == 1, f"Expected exit 1, got {result.exit_code}: {result.output}"

    def test_fail_on_exits_zero_with_no_findings(self) -> None:
        """--fail-on should exit 0 when there are no findings."""
        with patch("nuguard.analysis.public_api.run_analysis", _mock_run_analysis([])):
            result = runner.invoke(
                app,
                ["analyze", "--sbom", str(_FIXTURE_SBOM), "--fail-on", "low",
                 *_SKIP_SCANNERS],
            )
        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"

    def test_default_fail_on_is_high(self) -> None:
        """Without --fail-on, the default threshold is 'high' (exit 1 for high+)."""
        findings = [_make_finding("high")]
        with patch("nuguard.analysis.public_api.run_analysis", _mock_run_analysis(findings)):
            result = runner.invoke(
                app,
                ["analyze", "--sbom", str(_FIXTURE_SBOM), *_SKIP_SCANNERS],
            )
        assert result.exit_code == 1, f"Expected exit 1 with default fail-on, got {result.exit_code}"

    def test_fail_on_critical_exits_one_for_critical_finding(self) -> None:
        """--fail-on critical should exit 1 when a critical finding exists."""
        findings = [_make_finding("critical")]
        with patch("nuguard.analysis.public_api.run_analysis", _mock_run_analysis(findings)):
            result = runner.invoke(
                app,
                ["analyze", "--sbom", str(_FIXTURE_SBOM), "--fail-on", "critical",
                 *_SKIP_SCANNERS],
            )
        assert result.exit_code == 1, f"Expected exit 1, got {result.exit_code}: {result.output}"
