"""Tests for the gitleaks secret-scanner plugin: parsing, command shape, and wiring."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from nuguard.analysis.models import AnalysisResult
from nuguard.analysis.plugins import gitleaks_scanner as gl
from nuguard.analysis.plugins.gitleaks_scanner import (
    GitleaksScannerPlugin,
    _gitleaks_command,
    _parse_gitleaks_output,
)
from nuguard.analysis.public_api import AnalysisRunRequest

# Shaped like an AWS access key but not on gitleaks' allowlist (the docs
# example key AKIAIOSFODNN7EXAMPLE is allowlisted and would not be reported).
_FAKE_SECRET = "AKIAZ3Q7XK2M9PLRT4VN"

_LEAK = {
    "RuleID": "aws-access-token",
    "Description": "AWS Access Key",
    "File": "config.py",
    "StartLine": 3,
    "Secret": "REDACTED",
    "Match": "REDACTED",
    "Fingerprint": "config.py:aws-access-token:3",
}


def test_parse_emits_redacted_high_finding_per_leak() -> None:
    findings = _parse_gitleaks_output([_LEAK], "/src")
    assert len(findings) == 1
    finding = findings[0]
    assert finding["rule_id"] == "aws-access-token"
    assert finding["title"] == "Hardcoded secret: aws-access-token"
    assert finding["description"] == "AWS Access Key. The secret value is redacted in this report."
    assert finding["severity"] == "HIGH"
    assert finding["source"] == "gitleaks"
    assert finding["affected"] == ["config.py:3"]
    assert finding["scan_target"] == "/src"


def test_parse_never_copies_secret_fields() -> None:
    raw_leak = dict(_LEAK, Secret=_FAKE_SECRET, Match=_FAKE_SECRET)
    findings = _parse_gitleaks_output([raw_leak], "/src")
    serialized = json.dumps(findings)
    assert _FAKE_SECRET not in serialized
    assert "Secret" not in serialized
    assert "Match" not in serialized


def test_parse_empty_and_null_payloads_yield_no_findings() -> None:
    assert _parse_gitleaks_output([], "/src") == []
    assert _parse_gitleaks_output(None, "/src") == []


@pytest.mark.parametrize("payload", [{"unexpected": True}, "text", [1, 2]])
def test_parse_rejects_unexpected_shapes(payload: Any) -> None:
    with pytest.raises(ValueError, match="Invalid gitleaks output shape"):
        _parse_gitleaks_output(payload, "/src")


def test_parse_falls_back_to_scan_path_when_file_missing() -> None:
    leak = {"RuleID": "generic-api-key", "Description": "Generic API Key"}
    findings = _parse_gitleaks_output([leak], "/src")
    assert findings[0]["affected"] == ["/src"]


def test_command_redacts_and_never_fails_on_leaks() -> None:
    cmd = _gitleaks_command("/usr/bin/gitleaks", "/src")
    assert cmd[:3] == ["/usr/bin/gitleaks", "dir", "/src"]
    assert "--redact" in cmd
    assert cmd[cmd.index("--exit-code") + 1] == "0"
    assert cmd[cmd.index("--report-path") + 1] == "-"
    assert cmd[cmd.index("--report-format") + 1] == "json"


def test_run_is_skipped_when_binary_missing() -> None:
    with patch.object(gl.shutil, "which", return_value=None):
        result = GitleaksScannerPlugin().run({"nodes": []}, {})
    assert result.status == "skipped"
    assert "not installed" in (result.message or "")


def test_run_is_skipped_without_source_paths() -> None:
    with patch.object(gl.shutil, "which", return_value="/bin/gitleaks"):
        result = GitleaksScannerPlugin().run({"nodes": []}, {})
    assert result.status == "skipped"
    assert "no source paths" in (result.message or "")


def test_run_passes_timeouts_to_runner(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def _fake_run_scanner(tool, paths, command, parse, *, timeout, total_timeout):  # type: ignore[no-untyped-def]
        captured.update(tool=tool, paths=set(paths), timeout=timeout, total_timeout=total_timeout)
        return AnalysisResult(status="ok", plugin=tool, message="fake")

    sbom = {"nodes": [{"metadata": {"source_path": str(tmp_path)}}]}
    config = {"gitleaks_timeout": 7.5, "gitleaks_total_timeout": 42.0}
    with (
        patch.object(gl.shutil, "which", return_value="/bin/gitleaks"),
        patch.object(gl, "run_scanner", side_effect=_fake_run_scanner),
    ):
        GitleaksScannerPlugin().run(sbom, config)

    assert captured["tool"] == "gitleaks"
    assert captured["paths"] == {str(tmp_path)}
    assert captured["timeout"] == 7.5
    assert captured["total_timeout"] == 42.0


_requires_gitleaks = pytest.mark.skipif(
    shutil.which("gitleaks") is None, reason="gitleaks binary not on PATH"
)


@_requires_gitleaks
def test_real_scan_reports_leak_without_exposing_secret(tmp_path: Path) -> None:
    (tmp_path / "config.py").write_text(f'AWS_KEY = "{_FAKE_SECRET}"\n', encoding="utf-8")
    sbom = {"nodes": [{"metadata": {"source_path": str(tmp_path)}}]}

    result = GitleaksScannerPlugin().run(sbom, {})

    assert result.status != "error", result.message
    assert result.findings, "expected at least one gitleaks finding"
    assert all(f["severity"] == "HIGH" for f in result.findings)
    assert any("config.py" in f["affected"][0] for f in result.findings)
    serialized = json.dumps(result.model_dump(mode="json"))
    assert _FAKE_SECRET not in serialized


@_requires_gitleaks
def test_real_scan_of_clean_directory_has_no_findings(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    sbom = {"nodes": [{"metadata": {"source_path": str(tmp_path)}}]}

    result = GitleaksScannerPlugin().run(sbom, {})

    assert result.status != "error", result.message
    assert result.findings == []


def test_analysis_request_gitleaks_defaults_and_validation() -> None:
    req = AnalysisRunRequest()
    assert req.enable_gitleaks is True
    assert req.gitleaks_timeout == 120.0
    assert req.gitleaks_total_timeout == 300.0

    with pytest.raises(ValidationError):
        AnalysisRunRequest(gitleaks_timeout=0)
    with pytest.raises(ValidationError):
        AnalysisRunRequest(gitleaks_total_timeout=-1)


def test_gitleaks_is_a_registered_plugin_name() -> None:
    assert GitleaksScannerPlugin.name == "gitleaks"
