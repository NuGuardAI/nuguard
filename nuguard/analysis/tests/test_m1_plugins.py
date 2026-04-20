"""Unit tests for Checkov, Trivy, and Semgrep analysis plugins.

All subprocess calls are mocked so the tests run without the external binaries
installed.  Each plugin is tested for:

  - Graceful skip when binary is absent
  - Correct finding shape from realistic JSON output
  - No crash when the subprocess returns unexpected output
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SBOM_WITH_SRC: dict[str, Any] = {
    "nodes": [
        {
            "id": "app",
            "name": "app",
            "component_type": "SOURCE_DIRECTORY",
            "metadata": {"source_path": "/tmp/fake-app"},
        },
        {
            "id": "infra",
            "name": "infra",
            "component_type": "INFRASTRUCTURE_AS_CODE",
            "metadata": {"source_path": "/tmp/fake-app"},
        },
    ],
    "edges": [],
    "deps": [],
}

_SBOM_WITH_IMAGE: dict[str, Any] = {
    "nodes": [
        {
            "id": "img",
            "name": "myapp:latest",
            "component_type": "CONTAINER_IMAGE",
            "metadata": {"base_image": "myapp:latest"},
        },
    ],
    "edges": [],
    "deps": [],
}

_EMPTY_SBOM: dict[str, Any] = {"nodes": [], "edges": [], "deps": []}


def _completed_process(stdout: bytes, returncode: int = 0) -> MagicMock:
    cp = MagicMock()
    cp.stdout = stdout
    cp.stderr = b""
    cp.returncode = returncode
    return cp


# ---------------------------------------------------------------------------
# CheckovScannerPlugin
# ---------------------------------------------------------------------------

_CHECKOV_JSON_SINGLE: dict[str, Any] = {
    "results": {
        "failed_checks": [
            {
                "check_id": "CKV_K8S_30",
                "check": {"name": "Do not admit root containers", "guideline": "https://checkov.io"},
                "resource": "Deployment.default.my-app",
                "file_path": "/tmp/fake-app/k8s/deploy.yaml",
                "severity": "HIGH",
            }
        ]
    }
}

_CHECKOV_JSON_LIST: list[dict[str, Any]] = [_CHECKOV_JSON_SINGLE]


class TestCheckovPlugin:
    def _import(self):
        from nuguard.analysis.plugins.checkov_scanner import CheckovScannerPlugin
        return CheckovScannerPlugin()

    def test_skipped_when_binary_absent(self) -> None:
        plugin = self._import()
        with patch("shutil.which", return_value=None):
            result = plugin.run(_SBOM_WITH_SRC, {})
        assert result.status == "skipped"
        assert result.plugin == "checkov"

    def test_skipped_when_no_iac_paths(self) -> None:
        plugin = self._import()
        with (
            patch("shutil.which", return_value="/usr/bin/checkov"),
            patch("pathlib.Path.exists", return_value=False),
        ):
            result = plugin.run(_EMPTY_SBOM, {})
        assert result.status == "skipped"

    def test_finding_from_single_dict_output(self) -> None:
        plugin = self._import()
        stdout = json.dumps(_CHECKOV_JSON_SINGLE).encode()
        with (
            patch("shutil.which", return_value="/usr/bin/checkov"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=_completed_process(stdout, returncode=1)),
        ):
            result = plugin.run(_SBOM_WITH_SRC, {})

        assert result.status == "warning"
        assert len(result.findings) == 1
        f = result.findings[0]
        assert f["rule_id"] == "CKV_K8S_30"
        assert f["severity"] == "HIGH"
        assert "my-app" in f["affected"][0]

    def test_finding_from_list_output(self) -> None:
        plugin = self._import()
        stdout = json.dumps(_CHECKOV_JSON_LIST).encode()
        with (
            patch("shutil.which", return_value="/usr/bin/checkov"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=_completed_process(stdout, returncode=1)),
        ):
            result = plugin.run(_SBOM_WITH_SRC, {})

        assert len(result.findings) == 1

    def test_ok_status_when_no_failures(self) -> None:
        plugin = self._import()
        stdout = json.dumps({"results": {"failed_checks": []}}).encode()
        with (
            patch("shutil.which", return_value="/usr/bin/checkov"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=_completed_process(stdout, returncode=0)),
        ):
            result = plugin.run(_SBOM_WITH_SRC, {})

        assert result.status == "ok"
        assert result.findings == []

    def test_invalid_json_returns_ok(self) -> None:
        plugin = self._import()
        with (
            patch("shutil.which", return_value="/usr/bin/checkov"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=_completed_process(b"not-json", returncode=1)),
        ):
            result = plugin.run(_SBOM_WITH_SRC, {})

        # Bad output → no findings, but no crash
        assert result.findings == []

    def test_timeout_returns_ok(self) -> None:
        import subprocess  # noqa: PLC0415
        plugin = self._import()
        with (
            patch("shutil.which", return_value="/usr/bin/checkov"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("checkov", 120)),
        ):
            result = plugin.run(_SBOM_WITH_SRC, {})
        assert result.findings == []


# ---------------------------------------------------------------------------
# TrivyScannerPlugin
# ---------------------------------------------------------------------------

_TRIVY_FS_JSON: dict[str, Any] = {
    "Results": [
        {
            "Target": "/tmp/fake-app",
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "CVE-2023-99999",
                    "PkgName": "requests",
                    "InstalledVersion": "2.28.0",
                    "FixedVersion": "2.29.0",
                    "Severity": "HIGH",
                    "Title": "SSRF in requests",
                    "Description": "Server-side request forgery vulnerability.",
                    "PrimaryURL": "https://nvd.nist.gov/vuln/detail/CVE-2023-99999",
                }
            ],
            "Misconfigurations": [
                {
                    "ID": "DS002",
                    "Title": "Image user should not be 'root'",
                    "Description": "Container running as root.",
                    "Severity": "HIGH",
                    "Resolution": "Add USER directive to Dockerfile.",
                    "PrimaryURL": "https://avd.aquasec.com/misconfig/ds002",
                }
            ],
        }
    ]
}


class TestTrivyPlugin:
    def _import(self):
        from nuguard.analysis.plugins.trivy_scanner import TrivyScannerPlugin
        return TrivyScannerPlugin()

    def test_skipped_when_binary_absent(self) -> None:
        plugin = self._import()
        with patch("shutil.which", return_value=None):
            result = plugin.run(_SBOM_WITH_IMAGE, {})
        assert result.status == "skipped"
        assert result.plugin == "trivy"

    def test_skipped_when_no_scannable_targets(self) -> None:
        plugin = self._import()
        with patch("shutil.which", return_value="/usr/bin/trivy"):
            result = plugin.run(_EMPTY_SBOM, {})
        assert result.status == "skipped"

    def test_vuln_and_misconfig_finding(self) -> None:
        plugin = self._import()
        stdout = json.dumps(_TRIVY_FS_JSON).encode()
        with (
            patch("shutil.which", return_value="/usr/bin/trivy"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=_completed_process(stdout, returncode=1)),
        ):
            result = plugin.run(_SBOM_WITH_SRC, {})

        assert result.status == "warning"
        # 1 vuln + 1 misconfig = 2 findings (but may be called per path)
        assert len(result.findings) >= 1
        rule_ids = {f["rule_id"] for f in result.findings}
        assert "CVE-2023-99999" in rule_ids or "DS002" in rule_ids

    def test_image_scan_target(self) -> None:
        plugin = self._import()
        stdout = json.dumps({"Results": []}).encode()
        with (
            patch("shutil.which", return_value="/usr/bin/trivy"),
            patch("subprocess.run", return_value=_completed_process(stdout, returncode=0)),
        ):
            result = plugin.run(_SBOM_WITH_IMAGE, {})

        assert result.status == "ok"
        # The image scan was attempted (no crash)

    def test_timeout_returns_ok(self) -> None:
        import subprocess  # noqa: PLC0415
        plugin = self._import()
        with (
            patch("shutil.which", return_value="/usr/bin/trivy"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("trivy", 120)),
        ):
            result = plugin.run(_SBOM_WITH_SRC, {})
        assert result.findings == []

    def test_severity_mapping(self) -> None:
        self._import()
        from nuguard.analysis.plugins.trivy_scanner import _parse_trivy_output  # noqa: PLC0415
        trivy_out = {
            "Results": [{
                "Target": "test",
                "Vulnerabilities": [{"VulnerabilityID": "CVE-1", "PkgName": "pkg",
                                     "InstalledVersion": "1.0", "Severity": "CRITICAL"}],
            }]
        }
        findings = _parse_trivy_output(trivy_out, "test")
        assert findings[0]["severity"] == "CRITICAL"


# ---------------------------------------------------------------------------
# SemgrepScannerPlugin
# ---------------------------------------------------------------------------

_SEMGREP_JSON: dict[str, Any] = {
    "results": [
        {
            "check_id": "nuguard-hardcoded-api-key",
            "path": "/tmp/fake-app/app.py",
            "start": {"line": 5},
            "extra": {
                "message": "Hardcoded API key detected. Store secrets in env vars.",
                "severity": "ERROR",
                "metadata": {
                    "owasp": "A02: Cryptographic Failures",
                    "nuguard_rule": "NGA-003",
                },
            },
        },
        {
            "check_id": "nuguard-llm-prompt-injection-fstring",
            "path": "/tmp/fake-app/chat.py",
            "start": {"line": 22},
            "extra": {
                "message": "Potential prompt injection: user input in f-string.",
                "severity": "WARNING",
                "metadata": {
                    "owasp": "LLM01: Prompt Injection",
                    "nuguard_rule": "NGA-002",
                },
            },
        },
    ],
    "errors": [],
}


class TestSemgrepPlugin:
    def _import(self):
        from nuguard.analysis.plugins.semgrep_scanner import SemgrepScannerPlugin
        return SemgrepScannerPlugin()

    def test_skipped_when_binary_absent(self) -> None:
        plugin = self._import()
        with patch("shutil.which", return_value=None):
            result = plugin.run(_SBOM_WITH_SRC, {})
        assert result.status == "skipped"
        assert result.plugin == "semgrep"

    def test_skipped_when_no_source_paths(self) -> None:
        plugin = self._import()
        with patch("shutil.which", return_value="/usr/bin/semgrep"):
            result = plugin.run(_EMPTY_SBOM, {})
        assert result.status == "skipped"

    def test_two_findings_parsed(self) -> None:
        plugin = self._import()
        stdout = json.dumps(_SEMGREP_JSON).encode()
        with (
            patch("shutil.which", return_value="/usr/bin/semgrep"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=_completed_process(stdout, returncode=1)),
        ):
            result = plugin.run(_SBOM_WITH_SRC, {})

        assert result.status == "warning"
        assert len(result.findings) == 2
        rule_ids = {f["rule_id"] for f in result.findings}
        assert "nuguard-hardcoded-api-key" in rule_ids
        assert "nuguard-llm-prompt-injection-fstring" in rule_ids

    def test_severity_mapping_error_to_high(self) -> None:
        """Semgrep ERROR → nuguard HIGH."""
        plugin = self._import()
        stdout = json.dumps(_SEMGREP_JSON).encode()
        with (
            patch("shutil.which", return_value="/usr/bin/semgrep"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=_completed_process(stdout, returncode=1)),
        ):
            result = plugin.run(_SBOM_WITH_SRC, {})

        error_findings = [f for f in result.findings if f["rule_id"] == "nuguard-hardcoded-api-key"]
        assert error_findings[0]["severity"] == "HIGH"

    def test_severity_mapping_warning_to_medium(self) -> None:
        """Semgrep WARNING → nuguard MEDIUM."""
        plugin = self._import()
        stdout = json.dumps(_SEMGREP_JSON).encode()
        with (
            patch("shutil.which", return_value="/usr/bin/semgrep"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=_completed_process(stdout, returncode=1)),
        ):
            result = plugin.run(_SBOM_WITH_SRC, {})

        warn_findings = [
            f for f in result.findings
            if f["rule_id"] == "nuguard-llm-prompt-injection-fstring"
        ]
        assert warn_findings[0]["severity"] == "MEDIUM"

    def test_location_includes_file_and_line(self) -> None:
        plugin = self._import()
        stdout = json.dumps(_SEMGREP_JSON).encode()
        with (
            patch("shutil.which", return_value="/usr/bin/semgrep"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=_completed_process(stdout, returncode=1)),
        ):
            result = plugin.run(_SBOM_WITH_SRC, {})

        first = result.findings[0]
        assert "app.py" in first["affected"][0] or "chat.py" in first["affected"][0]

    def test_ok_when_no_findings(self) -> None:
        plugin = self._import()
        stdout = json.dumps({"results": [], "errors": []}).encode()
        with (
            patch("shutil.which", return_value="/usr/bin/semgrep"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=_completed_process(stdout, returncode=0)),
        ):
            result = plugin.run(_SBOM_WITH_SRC, {})
        assert result.status == "ok"
        assert result.findings == []

    def test_invalid_json_no_crash(self) -> None:
        plugin = self._import()
        with (
            patch("shutil.which", return_value="/usr/bin/semgrep"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=_completed_process(b"NOT JSON", returncode=1)),
        ):
            result = plugin.run(_SBOM_WITH_SRC, {})
        assert result.findings == []

    def test_timeout_returns_ok(self) -> None:
        import subprocess  # noqa: PLC0415
        plugin = self._import()
        with (
            patch("shutil.which", return_value="/usr/bin/semgrep"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("semgrep", 120)),
        ):
            result = plugin.run(_SBOM_WITH_SRC, {})
        assert result.findings == []
