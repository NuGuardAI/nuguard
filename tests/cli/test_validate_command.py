"""CLI smoke tests for ``nuguard validate``."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nuguard.cli.main import app
from nuguard.models.validate import CapabilityMap, ValidateRunResult

runner = CliRunner()


def test_validate_requires_target() -> None:
    """nuguard validate exits non-zero and mentions target when no target is configured."""
    result = runner.invoke(app, ["validate"])
    assert result.exit_code != 0, result.output
    assert "target" in result.output.lower()


def _write_validate_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "nuguard.yaml"
    cfg.write_text("validate:\n  target: http://localhost:9999\n", encoding="utf-8")
    return cfg


def _fake_validate_result() -> ValidateRunResult:
    return ValidateRunResult(
        run_id="test-run",
        findings=[{"severity": "medium", "title": "Sample finding"}],
        capability_map=CapabilityMap(
            run_id="test-run",
            built_at=datetime.now(UTC),
            entries=[],
        ),
        policy_records=[],
        scenarios_executed=1,
        scan_outcome="findings",
    )


async def _fake_run_validate(**_: object) -> ValidateRunResult:
    return _fake_validate_result()


def test_validate_multiple_formats_require_output(tmp_path: Path) -> None:
    cfg = _write_validate_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "validate",
            "--config",
            str(cfg),
            "--format",
            "json",
            "--format",
            "markdown",
        ],
    )
    assert result.exit_code == 2
    assert "--output is required" in result.output


def test_validate_multiple_formats_write_multiple_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import nuguard.cli.commands.validate as validate_cmd

    monkeypatch.setattr(validate_cmd, "_run_validate", _fake_run_validate)
    cfg = _write_validate_config(tmp_path)
    out_base = tmp_path / "validate-report"

    result = runner.invoke(
        app,
        [
            "validate",
            "--config",
            str(cfg),
            "--format",
            "json",
            "--format",
            "markdown",
            "--output",
            str(out_base),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "validate-report.json").exists()
    assert (tmp_path / "validate-report.md").exists()
