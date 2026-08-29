"""Tests for StaticAnalyzer.analyze()'s concurrent detector dispatch.

Steps 2-6 (OSV, Grype, Checkov, Trivy, Semgrep) run concurrently via
asyncio.gather/to_thread instead of sequentially. These tests verify that
concurrency doesn't change *what* gets reported: the Tool Coverage table's
key order stays fixed regardless of which step finishes first, one step
failing doesn't drop the others' findings, and the final finding set is the
same as the old sequential pipeline would have produced.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from nuguard.analysis.static_analyzer import StaticAnalyzer
from nuguard.models.finding import Finding, Severity
from nuguard.sbom.models import AiSbomDocument


def _finding(finding_id: str) -> Finding:
    return Finding(finding_id=finding_id, title="t", severity=Severity.HIGH, description="d")


def _sbom() -> AiSbomDocument:
    return AiSbomDocument(target="x")


@pytest.mark.asyncio
async def test_tool_status_key_order_is_fixed_regardless_of_completion_order() -> None:
    analyzer = StaticAnalyzer(enable_atlas=False, enable_supply_chain=False)

    # Make grype the slowest step so it finishes *after* checkov/trivy/semgrep
    # despite being dispatched second — the tool_status key order must still
    # reflect display order (nga-rules, osv, grype, checkov, trivy, semgrep),
    # not completion order.
    def _slow_run_grype(*_a, **_k):
        import time
        time.sleep(0.05)
        return [_finding("grype-1")]

    with (
        patch.object(analyzer, "_run_nga", return_value=[_finding("nga-1")]),
        patch.object(analyzer, "_run_osv", return_value=[_finding("osv-1")]),
        patch.object(analyzer, "_run_grype", side_effect=_slow_run_grype),
        patch.object(analyzer, "_run_m1", return_value=[]),
        patch("shutil.which", return_value="/usr/bin/grype"),
    ):
        findings = await analyzer.analyze(_sbom())

    assert list(analyzer.tool_status.keys()) == [
        "nga-rules", "osv", "grype", "checkov", "trivy", "semgrep", "atlas", "supply-chain",
    ]
    ids = {f.finding_id for f in findings}
    assert {"nga-1", "osv-1", "grype-1"} <= ids


@pytest.mark.asyncio
async def test_one_step_raising_does_not_drop_the_others() -> None:
    analyzer = StaticAnalyzer(enable_atlas=False, enable_supply_chain=False)

    def _boom(*_a, **_k):
        raise RuntimeError("unexpected bug in the step wrapper")

    with (
        patch.object(analyzer, "_run_nga", return_value=[]),
        patch.object(analyzer, "_run_osv", side_effect=_boom),
        patch.object(analyzer, "_run_grype", return_value=[_finding("grype-1")]),
        patch.object(analyzer, "_run_m1", return_value=[]),
        patch("shutil.which", return_value="/usr/bin/grype"),
    ):
        findings = await analyzer.analyze(_sbom())

    assert {f.finding_id for f in findings} == {"grype-1"}
    assert analyzer.tool_status["osv"]["status"] == "error"
    assert analyzer.tool_status["grype"]["status"] == "ok"


@pytest.mark.asyncio
async def test_disabled_steps_are_marked_disabled_and_produce_no_findings() -> None:
    analyzer = StaticAnalyzer(
        enable_osv=False, enable_grype=False, enable_checkov=False,
        enable_trivy=False, enable_semgrep=False,
        enable_atlas=False, enable_supply_chain=False,
    )
    with patch.object(analyzer, "_run_nga", return_value=[]):
        findings = await analyzer.analyze(_sbom())

    assert findings == []
    for tool in ("osv", "grype", "checkov", "trivy", "semgrep", "atlas", "supply-chain"):
        assert analyzer.tool_status[tool]["status"] == "disabled"
