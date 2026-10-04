"""Scanner deadlines must bound real processes and preserve incomplete coverage."""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from nuguard.analysis.models import AnalysisResult
from nuguard.analysis.plugins import scanner_runtime as runtime
from nuguard.analysis.plugins.checkov_scanner import _checkov_command
from nuguard.analysis.plugins.semgrep_scanner import SemgrepScannerPlugin, _parse_semgrep_output
from nuguard.analysis.public_api import AnalysisRunRequest
from nuguard.analysis.static_analyzer import StaticAnalyzer
from nuguard.config import NuGuardConfig, _flatten_yaml


@pytest.fixture(autouse=True)
def capture_scanner_logs(monkeypatch):
    monkeypatch.setattr(runtime._log, "propagate", True)


@pytest.mark.parametrize(
    ("script", "reason", "code"),
    [
        ('print("{broken}")', "invalid_json", 0),
        ("import sys; sys.exit(2)", "exit_error", 2),
        ('print("{}")', None, 0),
        ('import sys; print("{}"); sys.exit(1)', None, 1),
    ],
)
def test_process_outcomes(script, reason, code):
    _, error, returncode = runtime._execute([sys.executable, "-c", script], 3, "scanner", "source")
    assert (error, returncode) == (reason, code)


def test_launch_error_is_sanitized(caplog):
    caplog.set_level(logging.INFO)
    _, error, _ = runtime._execute(["/nonexistent/fixture-secret"], 1, "scanner", "source")
    assert error == "process_error"
    assert "fixture-secret" not in caplog.text


def test_slow_process_logs_heartbeat(caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    now = [0.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: now[0])

    def wait(timeout):
        now[0] = 16.0
        raise subprocess.TimeoutExpired("scanner", timeout)

    with (
        patch.object(runtime.subprocess, "Popen") as launch,
        patch.object(runtime, "_kill_process_tree"),
    ):
        launch.return_value.wait.side_effect = wait
        _, error, _ = runtime._execute(["scanner"], 16, "semgrep", "app.py")
    assert error == "timeout"
    assert "Scanner running: tool=semgrep path='app.py' elapsed=16.00s" in caplog.text


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group assertion")
def test_timeout_kills_workers_and_reaps_parent(tmp_path):
    pid_file = tmp_path / "workers"
    script = (
        "import subprocess, sys, os, time; "
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "open(sys.argv[1], 'w').write(str(os.getpid()) + ' ' + str(p.pid)); time.sleep(60)"
    )
    started = time.monotonic()
    _, error, _ = runtime._execute(
        [sys.executable, "-c", script, str(pid_file)], 0.5, "scanner", "source"
    )
    assert error == "timeout"
    assert time.monotonic() - started < 3
    parent, child = map(int, pid_file.read_text().split())
    with pytest.raises(ProcessLookupError):
        os.kill(parent, 0)
    # A killed child can briefly remain a zombie until init reaps it.
    stat = Path(f"/proc/{child}/stat")
    for _ in range(50):
        if not stat.exists() or stat.read_text().split()[2] == "Z":
            break
        time.sleep(0.02)
    else:
        pytest.fail("Scanner worker survived the deadline")


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_output_limit_applies_to_both_streams(monkeypatch, stream):
    monkeypatch.setattr(runtime, "_MAX_OUTPUT_BYTES", 64)
    script = f'import sys; print("{{}}"); sys.{stream}.write("x" * 100)'
    assert (
        runtime._execute([sys.executable, "-c", script], 3, "scanner", "source")[1]
        == "output_limit"
    )


def test_partial_findings_and_reported_errors_are_preserved(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    data = {
        "results": [{"check_id": "test", "path": "app.py", "extra": {"message": "finding"}}],
        "errors": [{"message": "fixture-secret"}],
    }
    with patch.object(runtime, "_execute", return_value=(data, None, 0)):
        result = runtime.run_scanner(
            "semgrep", [str(tmp_path)], lambda p: [p], _parse_semgrep_output
        )
    assert result.status == "error"
    assert len(result.findings) == 1
    assert result.details["incomplete"] is True
    assert result.details["invocations"][0]["reason"] == "scan_errors"
    assert "fixture-secret" not in result.model_dump_json() + caplog.text
    assert "Scanner path start" in caplog.text
    assert "Scanner path end" in caplog.text
    assert "findings=1" in caplog.text


def test_total_budget_prevents_launching_remaining_paths(tmp_path, monkeypatch):
    paths = [tmp_path / "a.py", tmp_path / "b.py"]
    for path in paths:
        path.touch()
    now = [0.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: now[0])
    calls = []

    def execute(cmd, timeout, tool, path):
        calls.append(timeout)
        now[0] += timeout
        return None, "timeout", None

    monkeypatch.setattr(runtime, "_execute", execute)
    result = runtime.run_scanner(
        "scanner", map(str, paths), lambda p: [p], lambda d, p: [], timeout=120, total_timeout=3
    )
    assert calls == [3]
    assert result.status == "error"
    assert [i["reason"] for i in result.details["invocations"]] == ["timeout", "total_timeout"]


def test_directory_coverage_deduplicates_nested_paths(tmp_path):
    child = tmp_path / "app.py"
    child.touch()
    assert runtime.scan_paths([str(child), str(tmp_path), str(tmp_path / ".")]) == [str(tmp_path)]
    assert "-f" in _checkov_command("checkov", str(child))
    assert "-d" in _checkov_command("checkov", str(tmp_path))


def test_plugin_errors_do_not_become_clean_scans(tmp_path):
    with (
        patch("nuguard.analysis.plugins.semgrep_scanner._semgrep_path", return_value="semgrep"),
        patch.object(runtime, "_execute", return_value=({}, None, 0)),
    ):
        result = SemgrepScannerPlugin().run({}, {"source_path": str(tmp_path)})
    assert result.status == "error"
    assert result.details["invocations"][0]["reason"] == "invalid_output"


def test_analyzer_retains_partial_findings_and_error_status():
    result = AnalysisResult(
        status="error",
        plugin="semgrep",
        message="scan incomplete",
        findings=[
            {
                "rule_id": "fixture",
                "title": "Partial finding",
                "severity": "HIGH",
                "description": "Found before failure",
                "affected": ["app.py"],
            }
        ],
    )
    analyzer = StaticAnalyzer()
    with patch(
        "nuguard.analysis.plugins.semgrep_scanner.SemgrepScannerPlugin.run", return_value=result
    ):
        findings = analyzer._run_m1("semgrep", {})
    assert len(findings) == 1
    assert analyzer.tool_status["semgrep"]["status"] == "error"
    assert analyzer.tool_status["semgrep"]["findings"] == "1"


@pytest.mark.parametrize(
    "key", ["checkov_timeout", "checkov_total_timeout", "semgrep_timeout", "semgrep_total_timeout"]
)
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_deadline_validation_at_public_boundaries(key, value):
    with pytest.raises(ValidationError):
        AnalysisRunRequest(**{key: value})
    with pytest.raises(ValidationError):
        NuGuardConfig(**{f"analyze_{key}": value})
    with pytest.raises(ValueError):
        StaticAnalyzer(**{key: value})


def test_deadlines_yaml_and_json_roundtrip():
    values = {
        "checkov_timeout": 5,
        "checkov_total_timeout": 10,
        "semgrep_timeout": 7,
        "semgrep_total_timeout": 12,
    }
    config = NuGuardConfig(**_flatten_yaml({"analyze": values}))
    request = AnalysisRunRequest(**values)
    restored = AnalysisRunRequest.model_validate_json(request.model_dump_json())
    for key, value in values.items():
        assert getattr(config, f"analyze_{key}") == value
        assert getattr(restored, key) == value
