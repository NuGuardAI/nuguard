"""Tests for nuguard.redteam.public_api — the Pydantic-only entry point for
callers outside the CLI (v1 RedteamOrchestrator only).

Focuses on: (1) JSON round-tripping of the new request/result models,
(2) that run_redteam() constructs RedteamOrchestrator with exactly the
request's fields plus the separately-passed collaborators and reads back the
orchestrator's instance attributes into RedteamRunResult, proving no
functionality is lost relative to the CLI's `_run_orchestrator`, and (3) the
duplicated post-run scenario_filter block matches the CLI's copy.
"""
from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from nuguard.common.auth import AuthConfig
from nuguard.config import RedteamFindingTriggers
from nuguard.models.finding import Finding, Severity
from nuguard.models.health_report import TargetHealthReport
from nuguard.models.token_usage import TokenUsage
from nuguard.redteam.coverage.tracker import CoverageTracker
from nuguard.redteam.public_api import RedteamRunRequest, RedteamRunResult, run_redteam
from nuguard.redteam.target.canary import CanaryConfig


def _finding(goal_type: str = "prompt_driven_threat") -> Finding:
    return Finding(
        finding_id="f1",
        title="t",
        severity=Severity.HIGH,
        description="d",
        goal_type=goal_type,
    )


@dataclasses.dataclass
class _FakeScenarioRecord:
    title: str
    goal_type: str
    scenario_type: str = "x"
    description: str = ""
    impact_score: float = 0.0
    affected: str = ""
    chain_status: str = "completed"
    had_finding: bool = False


def _make_mock_orchestrator(findings: list[Finding]) -> MagicMock:
    instance = MagicMock()
    instance.run = AsyncMock(return_value=findings)
    instance.scenario_records = [_FakeScenarioRecord(title="s1", goal_type="prompt_driven_threat")]
    instance.scan_outcome = "high_findings"
    instance.config_notes = ["note1"]
    instance.llm_executive_summary = "summary"
    instance.scenarios_run = 3
    instance.input_tokens_used = 10
    instance.output_tokens_used = 20
    instance.token_usage = TokenUsage(input_tokens=10, output_tokens=20)
    instance.health_report = TargetHealthReport(target_url="http://x", endpoint="/api/chat", run_id="r1")
    instance.resolved_chat_path = "/api/chat"
    instance.resolved_chat_path_source = "sbom"
    instance.catalog_coverage = None
    tracker = CoverageTracker()
    tracker.record_generated("n1", "AGENT", "Agent1")
    instance._coverage_tracker = tracker
    return instance


# ---------------------------------------------------------------------------
# JSON round-trips
# ---------------------------------------------------------------------------


def test_redteam_run_request_json_roundtrip():
    req = RedteamRunRequest(
        target_url="http://x",
        canary_config=CanaryConfig(),
        auth_config=AuthConfig(type="none"),
        finding_triggers=RedteamFindingTriggers(),
        scenario_filter=["prompt-injection"],
    )
    restored = RedteamRunRequest.model_validate_json(req.model_dump_json())
    assert restored.target_url == "http://x"
    assert restored.scenario_filter == ["prompt-injection"]
    assert restored.auth_config is not None
    assert restored.auth_config.type == "none"
    assert restored.canary_config is not None


def test_redteam_run_request_defaults_match_orchestrator_constructor():
    req = RedteamRunRequest(target_url="http://x")
    assert req.profile == "ci"
    assert req.concurrency == 5
    assert req.chat_path == "/chat"
    assert req.discovery_max_turns == 3
    assert req.suppress_spa_html_auth_bypass is True
    assert req.codegen_escalation_enabled is True


def test_redteam_run_result_json_roundtrip():
    result = RedteamRunResult(
        findings=[_finding()],
        scenario_records=[{"title": "s1"}],
        scan_outcome="high_findings",
        config_notes=["note"],
        token_usage=TokenUsage(input_tokens=1, output_tokens=2),
        resolved_chat_path="/api/chat",
        resolved_chat_path_source="sbom",
        health_report=TargetHealthReport(target_url="http://x", endpoint="/api/chat", run_id="r1"),
    )
    restored = RedteamRunResult.model_validate_json(result.model_dump_json())
    assert restored.findings[0].goal_type == "prompt_driven_threat"
    assert restored.scan_outcome == "high_findings"
    assert restored.health_report is not None
    assert restored.health_report.run_id == "r1"


def test_redteam_run_result_rejects_invalid_scan_outcome():
    with pytest.raises(ValidationError):
        RedteamRunResult(
            findings=[],
            scenario_records=[],
            scan_outcome="bogus",
            config_notes=[],
            token_usage=TokenUsage(input_tokens=0, output_tokens=0),
            resolved_chat_path="/c",
            resolved_chat_path_source="sbom",
        )


# ---------------------------------------------------------------------------
# run_redteam — thin-wrapper parity with RedteamOrchestrator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_redteam_constructs_orchestrator_and_builds_result():
    findings = [_finding("prompt_driven_threat"), _finding("data_exfiltration")]
    mock_instance = _make_mock_orchestrator(findings)
    sbom = MagicMock()
    policy = MagicMock()

    with patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls:
        mock_cls.return_value = mock_instance
        request = RedteamRunRequest(target_url="http://target", profile="standard")
        result = await run_redteam(request, sbom=sbom, policy=policy)

    mock_cls.assert_called_once()
    _, kwargs = mock_cls.call_args
    assert kwargs["sbom"] is sbom
    assert kwargs["policy"] is policy
    assert kwargs["target_url"] == "http://target"
    assert kwargs["profile"] == "standard"

    assert isinstance(result, RedteamRunResult)
    assert len(result.findings) == 2
    assert result.scenario_records == [dataclasses.asdict(mock_instance.scenario_records[0])]
    assert result.scan_outcome == "high_findings"
    assert result.config_notes == ["note1"]
    assert result.llm_executive_summary == "summary"
    # llm_coding_brief is computed by run_redteam itself (not read off the
    # orchestrator) and only populated when eval_llm is supplied — not the
    # case here.
    assert result.llm_coding_brief is None
    assert result.scenarios_run == 3
    assert result.token_usage.input_tokens == 10
    assert result.health_report is not None
    assert result.health_report.run_id == "r1"
    assert result.resolved_chat_path == "/api/chat"
    assert result.resolved_chat_path_source == "sbom"
    assert result.catalog_coverage is None
    assert result.coverage_tracker is not None
    assert result.coverage_tracker["nodes"][0]["name"] == "Agent1"


@pytest.mark.asyncio
async def test_run_redteam_passes_config_scalar_and_embedded_model_fields():
    """Every RedteamRunRequest field must reach the orchestrator constructor by
    attribute (never via model_dump(), which would serialize nested Pydantic
    config objects into plain dicts and break the orchestrator's constructor)."""
    mock_instance = _make_mock_orchestrator([])

    canary = CanaryConfig()
    auth = AuthConfig(type="none")
    triggers = RedteamFindingTriggers()

    with patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls:
        mock_cls.return_value = mock_instance
        request = RedteamRunRequest(
            target_url="http://target",
            canary_config=canary,
            auth_config=auth,
            finding_triggers=triggers,
            guided_mutation_mode="soft",
            tree_breadth=2,
        )
        await run_redteam(request, sbom=MagicMock())

    _, kwargs = mock_cls.call_args
    assert kwargs["canary_config"] is canary
    assert kwargs["auth_config"] is auth
    assert kwargs["finding_triggers"] is triggers
    assert kwargs["guided_mutation_mode"] == "soft"
    assert kwargs["tree_breadth"] == 2


@pytest.mark.asyncio
async def test_run_redteam_applies_scenario_filter_post_run_like_cli():
    """Regression test tying the duplicated scenario_filter block to the CLI's
    copy (nuguard/cli/commands/redteam.py:_run_orchestrator)."""
    findings = [_finding("prompt_driven_threat"), _finding("data_exfiltration")]
    mock_instance = _make_mock_orchestrator(findings)

    with patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls:
        mock_cls.return_value = mock_instance
        request = RedteamRunRequest(target_url="http://target", scenario_filter=["prompt-driven"])
        result = await run_redteam(request, sbom=MagicMock())

    assert len(result.findings) == 1
    assert result.findings[0].goal_type == "prompt_driven_threat"


@pytest.mark.asyncio
async def test_run_redteam_no_scenario_filter_keeps_all_findings():
    findings = [_finding("prompt_driven_threat"), _finding("data_exfiltration")]
    mock_instance = _make_mock_orchestrator(findings)

    with patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls:
        mock_cls.return_value = mock_instance
        request = RedteamRunRequest(target_url="http://target")
        result = await run_redteam(request, sbom=MagicMock())

    assert len(result.findings) == 2


@pytest.mark.asyncio
async def test_run_redteam_propagates_exceptions():
    """A full scan is not best-effort — real exceptions must not be swallowed."""
    with patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls:
        mock_cls.return_value.run = AsyncMock(side_effect=RuntimeError("target unreachable"))
        request = RedteamRunRequest(target_url="http://target")
        with pytest.raises(RuntimeError, match="target unreachable"):
            await run_redteam(request, sbom=MagicMock())


# ---------------------------------------------------------------------------
# remediation_plan — structured, machine-actionable remediation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_redteam_populates_remediation_plan():
    """Findings + a real SBOM should produce structured RemediationArtefact objects,
    via the same RemediationSynthesizer the CLI uses for its report's remediation plan."""
    from types import SimpleNamespace

    from nuguard.remediation.models import RemediationArtefact, RemediationArtefactType

    findings = [_finding("data_exfiltration")]
    mock_instance = _make_mock_orchestrator(findings)
    fake_artefact = RemediationArtefact(
        finding_ids=["f1"],
        component="unknown",
        component_type="AGENT",
        artefact_type=RemediationArtefactType.OUTPUT_GUARDRAIL,
        priority="high",
        rationale="d",
    )

    with (
        patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls,
        patch(
            "nuguard.remediation.synthesizer.RemediationSynthesizer.synthesize_findings_async"
        ) as mock_synth,
    ):
        mock_cls.return_value = mock_instance
        mock_synth.return_value = [fake_artefact]
        request = RedteamRunRequest(target_url="http://target")
        result = await run_redteam(request, sbom=SimpleNamespace(nodes=[], edges=[]))

    mock_synth.assert_awaited_once()
    (finding_dicts,), _ = mock_synth.await_args
    assert finding_dicts[0]["finding_id"] == "f1"
    assert finding_dicts[0]["goal_type"] == "data_exfiltration"
    assert result.remediation_plan == [fake_artefact]
    # Finding.remediation is backfilled from the matching artefact's rationale.
    assert result.findings[0].remediation == "d"


@pytest.mark.asyncio
async def test_run_redteam_remediation_plan_empty_without_findings():
    mock_instance = _make_mock_orchestrator([])

    with patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls:
        mock_cls.return_value = mock_instance
        request = RedteamRunRequest(target_url="http://target")
        result = await run_redteam(request, sbom=MagicMock())

    assert result.remediation_plan == []


@pytest.mark.asyncio
async def test_run_redteam_remediation_synthesis_failure_is_swallowed():
    """Remediation synthesis is best-effort — a failure must not fail the run."""
    findings = [_finding("data_exfiltration")]
    mock_instance = _make_mock_orchestrator(findings)

    with (
        patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls,
        patch(
            "nuguard.remediation.synthesizer.RemediationSynthesizer.synthesize_findings_async",
            side_effect=RuntimeError("boom"),
        ),
    ):
        mock_cls.return_value = mock_instance
        request = RedteamRunRequest(target_url="http://target")
        from types import SimpleNamespace

        result = await run_redteam(request, sbom=SimpleNamespace(nodes=[], edges=[]))

    assert result.remediation_plan == []
    assert len(result.findings) == 1
