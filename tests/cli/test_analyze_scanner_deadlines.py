"""CLI exposes scanner deadlines and fails visibly when coverage is incomplete."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from nuguard.analysis.public_api import AnalysisRunResult
from nuguard.cli.main import app


@pytest.mark.parametrize("tool", ["checkov", "semgrep"])
def test_incomplete_scanner_writes_report_and_exits_error(tmp_path, tool):
    config = tmp_path / "nuguard.yaml"
    config.write_text("analyze:\n  checkov_timeout: 9\n  semgrep_total_timeout: 18\n")
    output = tmp_path / "report.json"
    captured = {}

    async def analyze(request, **kwargs):
        captured.update(request.model_dump())
        return AnalysisRunResult(
            findings=[], tool_status={tool: {"status": "error", "reason": "scan incomplete"}}
        )

    sbom = Path(__file__).resolve().parents[2] / "nuguard/analysis/tests/fixtures/minimal.sbom.json"
    with patch("nuguard.analysis.public_api.run_analysis", side_effect=analyze):
        result = CliRunner().invoke(
            app,
            [
                "analyze",
                "--sbom",
                str(sbom),
                "--config",
                str(config),
                "--checkov-total-timeout",
                "15",
                "--semgrep-timeout",
                "6",
                "--format",
                "json",
                "--output",
                str(output),
            ],
            catch_exceptions=False,
        )
    assert result.exit_code == 2, result.output
    assert output.exists()
    assert '"status": "error"' in output.read_text()
    assert "Analysis incomplete" in result.output
    assert captured["checkov_timeout"] == 9
    assert captured["checkov_total_timeout"] == 15
    assert captured["semgrep_timeout"] == 6
    assert captured["semgrep_total_timeout"] == 18
