"""Coverage honesty: a tool that did not finish must never be reported ``ok``.

Covers the typed ``ToolRun`` view and the legacy ``tool_status`` dict for
OSV / Grype / Trivy / NGA failures, offline mode, and not-applicable tools.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from nuguard.analysis.public_api import AnalysisRunRequest, run_analysis
from nuguard.analysis.static_analyzer import StaticAnalyzer
from nuguard.models.tool_run import ToolRun, ToolRunStatus
from nuguard.sbom.models import AiSbomDocument

_DEPS = [{"name": "requests", "version_spec": "2.19.0", "purl": "pkg:pypi/requests@2.19.0",
          "source_file": "requirements.txt", "group": "runtime"}]


def _sbom(deps: list[dict] | None = None) -> AiSbomDocument:
    return AiSbomDocument(target="x", deps=deps or [])


def _runs(analyzer: StaticAnalyzer) -> dict[str, ToolRun]:
    return {r.tool: r for r in analyzer.tool_runs}


def _only(**enabled: bool) -> StaticAnalyzer:
    flags = {f"enable_{t}": False for t in
             ("osv", "grype", "checkov", "trivy", "semgrep", "atlas", "supply_chain")}
    flags.update({f"enable_{k}": v for k, v in enabled.items()})
    return StaticAnalyzer(**flags)


def test_tool_run_requires_reason_unless_ok_or_disabled() -> None:
    ToolRun(tool="osv", status=ToolRunStatus.OK)
    ToolRun(tool="osv", status=ToolRunStatus.DISABLED)
    for status in ("failed", "skipped", "not_applicable", "timeout"):
        with pytest.raises(ValidationError):
            ToolRun(tool="osv", status=status)


@pytest.mark.asyncio
async def test_osv_network_failure_is_failed_not_ok() -> None:
    analyzer = _only(osv=True)
    with patch("nuguard.analysis.osv_client._post_json",
               side_effect=urllib.error.URLError("[Errno 8] nodename nor servname provided")):
        findings = await analyzer.analyze(_sbom(_DEPS))

    run = _runs(analyzer)["osv"]
    assert findings == []
    assert run.status is ToolRunStatus.FAILED
    assert run.reason == "network unreachable"
    assert analyzer.tool_status["osv"]["status"] == "error"


@pytest.mark.asyncio
async def test_osv_timeout_is_timeout() -> None:
    analyzer = _only(osv=True)
    with patch("nuguard.analysis.osv_client._post_json", side_effect=TimeoutError()):
        await analyzer.analyze(_sbom(_DEPS))
    assert _runs(analyzer)["osv"].status is ToolRunStatus.TIMEOUT


@pytest.mark.asyncio
async def test_osv_without_dependencies_is_not_applicable() -> None:
    analyzer = _only(osv=True)
    await analyzer.analyze(_sbom())
    run = _runs(analyzer)["osv"]
    assert run.status is ToolRunStatus.NOT_APPLICABLE
    assert run.reason


@pytest.mark.asyncio
async def test_osv_success_is_ok_with_dependency_location() -> None:
    analyzer = _only(osv=True)
    advisory = {"dep_name": "requests", "dep_version": "2.19.0", "purl": "pkg:pypi/requests@2.19.0",
                "advisory_id": "PYSEC-2018-28", "cve_ids": ["CVE-2018-18074"], "summary": "s",
                "severity": "HIGH", "affected_versions": "<2.20.0", "url": "u",
                "source_file": "requirements.txt"}
    with patch("nuguard.analysis.osv_client.query_osv", return_value=[advisory]):
        findings = await analyzer.analyze(_sbom(_DEPS))

    assert _runs(analyzer)["osv"].status is ToolRunStatus.OK
    (finding,) = findings
    assert finding.rule_id == "CVE-2018-18074"  # CVE alias preferred over PYSEC id
    assert finding.source_tool == "osv"
    assert finding.kind == "dependency"
    assert [loc.path for loc in finding.locations] == ["requirements.txt"]


@pytest.mark.asyncio
async def test_grype_bad_database_is_failed() -> None:
    analyzer = _only(grype=True)
    broken = subprocess.CompletedProcess(
        args=["grype"], returncode=2, stdout=b"", stderr=b"failed to load vulnerability db",
    )
    with (
        patch("shutil.which", return_value="/usr/bin/grype"),
        patch("nuguard.analysis.grype_client._grype_path", return_value="/usr/bin/grype"),
        patch("nuguard.analysis.grype_client.subprocess.run", return_value=broken),
        patch("nuguard.analysis.static_analyzer.tool_version", return_value=None),
    ):
        await analyzer.analyze(_sbom(_DEPS))

    run = _runs(analyzer)["grype"]
    assert run.status is ToolRunStatus.FAILED
    assert run.reason == "exit status 2"
    assert "vulnerability db" not in (run.reason or "")  # no stderr dump


@pytest.mark.asyncio
async def test_trivy_timeout_is_timeout_not_ok(tmp_path) -> None:
    (tmp_path / "app.py").write_text("print('x')\n")
    analyzer = _only(trivy=True)
    analyzer.source_path = tmp_path
    with (
        patch("shutil.which", return_value="/usr/bin/trivy"),
        patch("nuguard.analysis.plugins.trivy_scanner.subprocess.run",
              side_effect=subprocess.TimeoutExpired("trivy", 1)),
        patch("nuguard.analysis.static_analyzer.tool_version", return_value=None),
    ):
        await analyzer.analyze(_sbom(_DEPS))
    run = _runs(analyzer)["trivy"]
    assert run.status is ToolRunStatus.TIMEOUT
    assert analyzer.tool_status["trivy"]["status"] == "error"


@pytest.mark.asyncio
async def test_trivy_exit_error_is_failed(tmp_path) -> None:
    (tmp_path / "app.py").write_text("print('x')\n")
    analyzer = _only(trivy=True)
    analyzer.source_path = tmp_path
    broken = subprocess.CompletedProcess(args=["trivy"], returncode=1 + 1, stdout=b"", stderr=b"db")
    with (
        patch("shutil.which", return_value="/usr/bin/trivy"),
        patch("nuguard.analysis.plugins.trivy_scanner.subprocess.run", return_value=broken),
        patch("nuguard.analysis.static_analyzer.tool_version", return_value=None),
    ):
        await analyzer.analyze(_sbom(_DEPS))
    assert _runs(analyzer)["trivy"].status is ToolRunStatus.FAILED


@pytest.mark.asyncio
async def test_offline_mode_skips_network_tools() -> None:
    analyzer = StaticAnalyzer(enable_checkov=False, enable_semgrep=False,
                              enable_supply_chain=False, offline=True)
    with patch("nuguard.analysis.osv_client._post_json") as post:
        await analyzer.analyze(_sbom(_DEPS))
    post.assert_not_called()
    runs = _runs(analyzer)
    for tool in ("osv", "grype", "trivy"):
        assert runs[tool].status is ToolRunStatus.SKIPPED
        assert "offline" in (runs[tool].reason or "")


@pytest.mark.asyncio
async def test_missing_binaries_are_skipped_and_missing_targets_not_applicable(tmp_path) -> None:
    analyzer = _only(semgrep=True, checkov=True)
    with patch("shutil.which", return_value=None):
        await analyzer.analyze(_sbom())
    runs = _runs(analyzer)
    assert runs["semgrep"].status is ToolRunStatus.SKIPPED
    assert runs["checkov"].status is ToolRunStatus.SKIPPED

    analyzer = _only(semgrep=True, checkov=True)
    with patch("shutil.which", return_value="/usr/bin/tool"):
        await analyzer.analyze(_sbom())
    runs = _runs(analyzer)
    assert runs["semgrep"].status is ToolRunStatus.NOT_APPLICABLE
    assert runs["checkov"].status is ToolRunStatus.NOT_APPLICABLE


@pytest.mark.asyncio
async def test_nga_rule_error_marks_nga_failed() -> None:
    from nuguard.analysis.plugins import nga_rules

    def _boom(**_kw):
        raise RuntimeError("rule bug")

    analyzer = _only()
    with patch.object(nga_rules, "_RULES", [_boom, *nga_rules._RULES[1:]]):
        await analyzer.analyze(_sbom())
    run = _runs(analyzer)["nga-rules"]
    assert run.status is ToolRunStatus.FAILED
    assert "NGA-001" in (run.reason or "")


@pytest.mark.asyncio
async def test_every_tool_has_a_run_with_duration_in_display_order() -> None:
    analyzer = StaticAnalyzer(offline=True)
    with patch("shutil.which", return_value=None):
        await analyzer.analyze(_sbom())
    assert [r.tool for r in analyzer.tool_runs] == [
        "nga-rules", "osv", "grype", "checkov", "trivy", "semgrep", "atlas", "supply-chain",
    ]
    assert all(r.duration_ms is not None for r in analyzer.tool_runs)
    assert next(r for r in analyzer.tool_runs if r.tool == "nga-rules").version


@pytest.mark.asyncio
async def test_public_api_exposes_tool_runs_and_round_trips() -> None:
    request = AnalysisRunRequest(offline=True, enable_semgrep=False, enable_checkov=False)
    result = await run_analysis(request, sbom=_sbom(_DEPS))
    payload = json.loads(result.model_dump_json())
    statuses = {r["tool"]: r["status"] for r in payload["tool_runs"]}
    assert statuses["osv"] == "skipped"
    assert statuses["checkov"] == "disabled"
    assert type(result).model_validate(payload).tool_runs == result.tool_runs


def test_scanner_reason_never_contains_absolute_paths() -> None:
    analyzer = StaticAnalyzer()
    result = SimpleNamespace(
        status="error", message="offline scan failed: [Errno 2] /home/user/secret/x.json",
        findings=[], details={},
    )
    with patch("nuguard.analysis.plugins.supply_chain.SupplyChainPlugin.run", return_value=result):
        analyzer._run_supply_chain({})
    reason = analyzer.tool_status["supply-chain"]["reason"]
    assert "/home/user" not in reason


def test_checkov_summary_only_output_means_no_findings() -> None:
    """Checkov prints only a summary when a directory has nothing to check."""
    from nuguard.analysis.plugins.checkov_scanner import _parse_checkov_output

    summary = {"passed": 0, "failed": 0, "skipped": 0, "parsing_errors": 0,
               "resource_count": 0, "checkov_version": "3.3.9"}
    assert _parse_checkov_output(summary, "/repo") == []
    assert _parse_checkov_output([summary], "/repo") == []
    with pytest.raises(ValueError):
        _parse_checkov_output({"unexpected": True}, "/repo")
