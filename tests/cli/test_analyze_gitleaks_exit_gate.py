"""Exit-gate tests: a failed gitleaks scan must count as incomplete coverage.

Mirrors the Checkov and Semgrep behaviour, where an errored scanner makes
``nuguard analyze`` exit 2 even when no findings are present.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from nuguard.analysis.public_api import AnalysisRunResult
from nuguard.cli.main import app

runner = CliRunner()

_FIXTURE_SBOM = (
    Path(__file__).parent.parent.parent
    / "nuguard"
    / "analysis"
    / "tests"
    / "fixtures"
    / "minimal.sbom.json"
)

_SKIP_SCANNERS = [
    "--no-atlas", "--no-osv", "--no-grype",
    "--no-checkov", "--no-trivy", "--no-semgrep",
    "--no-supply-chain", "--no-pip-audit",
]


def _mock_with_status(tool_status: dict[str, dict[str, str]]) -> AsyncMock:
    async def _run(request, sbom=None, llm_client=None):  # noqa: ANN001
        return AnalysisRunResult(findings=[], tool_status=tool_status)

    return AsyncMock(side_effect=_run)


class TestGitleaksExitGate:
    """A gitleaks error is incomplete coverage and exits 2."""

    def test_gitleaks_error_exits_two(self) -> None:
        status = {"gitleaks": {"status": "error", "detail": "boom"}}
        with patch("nuguard.analysis.public_api.run_analysis", _mock_with_status(status)):
            result = runner.invoke(
                app,
                ["analyze", "--sbom", str(_FIXTURE_SBOM), *_SKIP_SCANNERS],
            )
        assert result.exit_code == 2, f"Expected exit 2, got {result.exit_code}: {result.output}"
        assert "gitleaks" in result.output

    def test_gitleaks_ok_exits_zero(self) -> None:
        status = {"gitleaks": {"status": "ok"}}
        with patch("nuguard.analysis.public_api.run_analysis", _mock_with_status(status)):
            result = runner.invoke(
                app,
                ["analyze", "--sbom", str(_FIXTURE_SBOM), *_SKIP_SCANNERS],
            )
        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}: {result.output}"
