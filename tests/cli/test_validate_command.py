"""CLI smoke tests for ``nuguard validate``."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nuguard.cli.main import app
from nuguard.models.validate import CapabilityMap, TurnPolicyRecord, ValidateRunResult

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
        policy_records=[
            TurnPolicyRecord(
                turn=1,
                prompt="prompt text",
                response="response text",
                scenario_name="scenario-a",
                scenario_type="policy_compliance",
                violations=[{"severity": "high", "evidence": "violation evidence"}],
                passed=False,
            )
        ],
        scenarios_executed=1,
        scan_outcome="findings",
        effective_endpoint="/api/agent/chat",
        target_endpoint_source="probe",
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


def test_validate_json_includes_verbose_flag_and_diagnostics_when_verbose(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import json as _json

    import nuguard.cli.commands.validate as validate_cmd

    monkeypatch.setattr(validate_cmd, "_run_validate", _fake_run_validate)
    cfg = _write_validate_config(tmp_path)
    out_base = tmp_path / "validate-verbose"

    result = runner.invoke(
        app,
        [
            "validate",
            "--config",
            str(cfg),
            "--format",
            "json",
            "--output",
            str(out_base),
            "--verbose",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(out_base.read_text(encoding="utf-8"))
    assert payload["_meta"]["verbose"] is True
    assert "diagnostics" in payload
    assert "scenario_traces" in payload["diagnostics"]


def test_validate_json_omits_diagnostics_when_not_verbose(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import json as _json

    import nuguard.cli.commands.validate as validate_cmd

    monkeypatch.setattr(validate_cmd, "_run_validate", _fake_run_validate)
    cfg = _write_validate_config(tmp_path)
    out_base = tmp_path / "validate-nonverbose"

    result = runner.invoke(
        app,
        [
            "validate",
            "--config",
            str(cfg),
            "--format",
            "json",
            "--output",
            str(out_base),
            "--no-verbose",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(out_base.read_text(encoding="utf-8"))
    assert payload["_meta"]["verbose"] is False
    assert "diagnostics" not in payload


def test_validate_json_uses_resolved_endpoint_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import json as _json

    import nuguard.cli.commands.validate as validate_cmd

    monkeypatch.setattr(validate_cmd, "_run_validate", _fake_run_validate)
    cfg = _write_validate_config(tmp_path)
    out_base = tmp_path / "validate-meta"

    result = runner.invoke(
        app,
        [
            "validate",
            "--config",
            str(cfg),
            "--format",
            "json",
            "--output",
            str(out_base),
        ],
    )
    assert result.exit_code == 0, result.output

    payload = _json.loads(out_base.read_text(encoding="utf-8"))
    assert payload["_meta"]["effective_endpoint"] == "/api/agent/chat"
    assert payload["_meta"]["target_endpoint_source"] == "probe"


def test_validate_creates_missing_parent_dir_for_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``nuguard validate --output <nested>/out.json`` must auto-create parents.

    Regression: validate wrote the output file with a bare ``write_text``, so
    a missing parent directory raised an uncaught FileNotFoundError traceback,
    unlike analyze / behavior / redteam / scan / sbom which all auto-create
    the parent (issue #233 unified behaviour).
    """
    import nuguard.cli.commands.validate as validate_cmd

    monkeypatch.setattr(validate_cmd, "_run_validate", _fake_run_validate)
    cfg = _write_validate_config(tmp_path)
    out_path = tmp_path / "a" / "b" / "c" / "results.json"
    assert not out_path.parent.exists()

    result = runner.invoke(
        app,
        [
            "validate",
            "--config", str(cfg),
            "--format", "json",
            "--output", str(out_path),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert out_path.exists(), (
        f"Expected validate report at {out_path}; stdout:\n{result.output}"
    )
