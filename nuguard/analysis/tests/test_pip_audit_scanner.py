"""Tests for the pip-audit plugin: output parsing, manifest selection and dedupe."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from nuguard.analysis.models import AnalysisResult
from nuguard.analysis.plugins import pip_audit_scanner as pa
from nuguard.analysis.plugins.pip_audit_scanner import (
    PipAuditScannerPlugin,
    _best_fix_version,
    _collect_manifest_targets,
    _parse_pip_audit_output,
    _pip_audit_command,
)

_CHROMADB_OUTPUT: dict[str, Any] = {
    "dependencies": [
        {
            "name": "chromadb",
            "version": "1.5.9",
            "vulns": [
                {
                    "id": "PYSEC-2026-3813",
                    "aliases": ["GHSA-2wm9-hf6c-p5cr", "CVE-2026-45830"],
                    "fix_versions": [],
                    "description": "Unauthenticated access in chromadb.",
                },
                {
                    "id": "PYSEC-2026-311",
                    "aliases": ["CVE-2026-45829", "GHSA-f4j7-r4q5-qw2c"],
                    "fix_versions": ["1.6.0", "1.5.10"],
                    "description": "Path traversal in chromadb.",
                },
            ],
        },
        {"name": "requests", "version": "2.32.0", "vulns": []},
        {"name": "skipped-pkg", "version": "1.0", "skip_reason": "not on index"},
    ],
    "fixes": [],
}


def test_parse_emits_one_finding_per_advisory_with_aliases_in_title() -> None:
    findings = _parse_pip_audit_output(_CHROMADB_OUTPUT, "/proj")

    assert [f["rule_id"] for f in findings] == ["PYSEC-2026-3813", "PYSEC-2026-311"]
    first = findings[0]
    assert first["source"] == "pip-audit"
    assert first["affected"] == ["pkg:pypi/chromadb@1.5.9"]
    # Aliases are in the title so the analyzer can join them with OSV/Trivy.
    assert "CVE-2026-45830" in first["title"]
    assert "GHSA-2wm9-hf6c-p5cr" in first["title"]
    assert first["cve_ids"] == ["CVE-2026-45830"]


def test_parse_severity_is_assumed_medium_and_documented() -> None:
    finding = _parse_pip_audit_output(_CHROMADB_OUTPUT, "/proj")[0]
    assert finding["severity"] == "MEDIUM"
    assert "defaulted to MEDIUM" in finding["description"]


def test_parse_picks_highest_fix_version_and_no_fix_when_absent() -> None:
    findings = _parse_pip_audit_output(_CHROMADB_OUTPUT, "/proj")
    assert findings[0]["fixed_version"] is None
    assert "fix available" not in findings[0]["remediation"]
    assert findings[1]["fixed_version"] == "1.6.0"
    assert "fix available: 1.6.0" in findings[1]["remediation"]


def test_best_fix_version_compares_numerically() -> None:
    assert _best_fix_version(["1.9.0", "1.10.0"]) == "1.10.0"
    assert _best_fix_version([]) is None


def test_parse_ignores_malformed_entries() -> None:
    assert _parse_pip_audit_output(["not", "a", "dict"], "/p") == []
    data = {"dependencies": [None, {"name": "x", "version": "1", "vulns": [{"no_id": 1}]}]}
    assert _parse_pip_audit_output(data, "/p") == []


def test_dedupe_collapses_repeated_rows_from_multiple_paths() -> None:
    data = {
        "dependencies": [
            {"name": "chromadb", "version": "1.5.9", "vulns": [_CHROMADB_OUTPUT["dependencies"][0]["vulns"][0]]},
            {"name": "chromadb", "version": "1.5.9", "vulns": [_CHROMADB_OUTPUT["dependencies"][0]["vulns"][0]]},
        ]
    }
    raw = _parse_pip_audit_output(data, "/p")
    assert len(raw) == 2
    assert len(pa._dedupe_findings(raw)) == 1


def test_command_uses_directory_or_requirements_flag(tmp_path: Path) -> None:
    req = tmp_path / "requirements.txt"
    req.write_text("requests==2.32.0\n", encoding="utf-8")
    dir_cmd = _pip_audit_command("/bin/pip-audit", str(tmp_path))
    file_cmd = _pip_audit_command("/bin/pip-audit", str(req))

    assert dir_cmd[:2] == ["/bin/pip-audit", str(tmp_path)]
    assert file_cmd[:3] == ["/bin/pip-audit", "-r", str(req)]
    for cmd in (dir_cmd, file_cmd):
        assert "--vulnerability-service" in cmd and "osv" in cmd
        assert "json" in cmd


def test_collect_targets_maps_pyproject_to_directory_and_requirements_to_file(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
    (app / "requirements.txt").write_text("requests\n", encoding="utf-8")
    sbom = {
        "target": str(tmp_path),
        "deps": [
            {"name": "chromadb", "purl": "pkg:pypi/chromadb@0.5.0", "source_file": "app/pyproject.toml"},
            {"name": "requests", "purl": "pkg:pypi/requests", "source_file": "app/requirements.txt"},
            {"name": "left-pad", "purl": "pkg:npm/left-pad", "source_file": "package.json"},
            {"name": "gone", "purl": "pkg:pypi/gone", "source_file": "missing/pyproject.toml"},
        ],
    }
    targets = _collect_manifest_targets(sbom, {})
    assert targets == {str(app), str(app / "requirements.txt")}


def test_run_is_skipped_when_binary_missing() -> None:
    with patch.object(pa, "_pip_audit_path", return_value=None):
        result = PipAuditScannerPlugin().run({"deps": []}, {})
    assert result.status == "skipped"
    assert "not installed" in (result.message or "")


def test_run_is_skipped_without_python_manifests() -> None:
    with patch.object(pa, "_pip_audit_path", return_value="/bin/pip-audit"):
        result = PipAuditScannerPlugin().run({"target": "/nonexistent", "deps": []}, {})
    assert result.status == "skipped"
    assert "no Python manifests" in (result.message or "")


def test_run_dedupes_scanner_findings_and_passes_timeouts(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    sbom = {
        "target": str(tmp_path),
        "deps": [
            {
                "name": "chromadb",
                "purl": "pkg:pypi/chromadb",
                "source_file": str(tmp_path / "pyproject.toml"),
            }
        ],
    }
    raw = _parse_pip_audit_output(_CHROMADB_OUTPUT, "/p")
    captured: dict[str, Any] = {}

    def _fake_run_scanner(tool, paths, command, parse, *, timeout, total_timeout):  # type: ignore[no-untyped-def]
        captured.update(timeout=timeout, total_timeout=total_timeout, paths=paths)
        return AnalysisResult(
            status="warning", plugin=tool, message="fake", findings=raw + raw
        )

    with (
        patch.object(pa, "_pip_audit_path", return_value="/bin/pip-audit"),
        patch.object(pa, "run_scanner", side_effect=_fake_run_scanner),
    ):
        result = PipAuditScannerPlugin().run(
            sbom, {"pip_audit_timeout": 42.0, "pip_audit_total_timeout": 99.0}
        )

    assert captured["timeout"] == 42.0
    assert captured["total_timeout"] == 99.0
    assert captured["paths"] == {str(tmp_path)}
    assert len(result.findings) == len(raw)
    assert result.details["total"] == len(raw)
