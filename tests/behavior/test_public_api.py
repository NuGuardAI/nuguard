"""Tests for nuguard.behavior.public_api — the Pydantic-only entry points for
callers outside the CLI.

These are thin-wrapper functions, so tests focus on: (1) JSON round-tripping
of the new request models, (2) that each wrapper constructs the underlying
class with exactly the request's fields (plus the separately-passed
collaborators) and forwards to the right method, proving no functionality is
lost relative to calling BehaviorAnalyzer/BehaviorRunner directly, and (3)
that the Literal-tightened `mode` field rejects invalid values.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from nuguard.behavior.models import (
    BehaviorAnalysisResult,
    BehaviorRunResult,
    BehaviorScenario,
    BehaviorScenarioType,
)
from nuguard.behavior.public_api import (
    BehaviorAnalysisRequest,
    BehaviorRunRequest,
    analyze_behavior,
    discover_behavior_profile,
    run_behavior_scenarios,
)
from nuguard.common.discovery import DiscoveredProfile
from nuguard.config import BehaviorConfig


def _scenario(name: str = "s1") -> BehaviorScenario:
    return BehaviorScenario(
        scenario_type=BehaviorScenarioType.INTENT_HAPPY_PATH,
        name=name,
        messages=["hello"],
    )


# ---------------------------------------------------------------------------
# JSON round-trips
# ---------------------------------------------------------------------------


def test_behavior_analysis_request_json_roundtrip():
    req = BehaviorAnalysisRequest(config=BehaviorConfig(target="http://localhost:9999"), mode="static")
    restored = BehaviorAnalysisRequest.model_validate_json(req.model_dump_json())
    assert restored.mode == "static"
    assert restored.config.target == "http://localhost:9999"


def test_behavior_analysis_request_default_mode():
    req = BehaviorAnalysisRequest(config=BehaviorConfig(target="http://localhost:9999"))
    assert req.mode == "static+dynamic"


def test_behavior_analysis_request_rejects_invalid_mode():
    with pytest.raises(ValidationError):
        BehaviorAnalysisRequest(config=BehaviorConfig(target="http://localhost:9999"), mode="bogus")


def test_behavior_run_request_json_roundtrip():
    req = BehaviorRunRequest(
        config=BehaviorConfig(target="http://localhost:9999"),
        scenarios=[_scenario("a"), _scenario("b")],
        pre_scan_profile=DiscoveredProfile(customer_name="Alice", ids=["ACCT-1"], source="live"),
    )
    restored = BehaviorRunRequest.model_validate_json(req.model_dump_json())
    assert [s.name for s in restored.scenarios] == ["a", "b"]
    assert restored.pre_scan_profile is not None
    assert restored.pre_scan_profile.customer_name == "Alice"
    assert restored.pre_scan_profile.source == "live"


def test_behavior_run_request_pre_scan_profile_defaults_none():
    req = BehaviorRunRequest(
        config=BehaviorConfig(target="http://localhost:9999"),
        scenarios=[_scenario()],
    )
    assert req.pre_scan_profile is None


# ---------------------------------------------------------------------------
# analyze_behavior — thin-wrapper parity with BehaviorAnalyzer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_behavior_constructs_analyzer_and_forwards_mode():
    sentinel_result = MagicMock(spec=BehaviorAnalysisResult)
    config = BehaviorConfig(target="http://localhost:9999")
    sbom = MagicMock()
    policy = MagicMock()
    controls = [MagicMock()]
    llm_client = MagicMock()

    with patch("nuguard.behavior.public_api.BehaviorAnalyzer") as mock_cls:
        mock_instance = mock_cls.return_value
        mock_instance.analyze = AsyncMock(return_value=sentinel_result)

        request = BehaviorAnalysisRequest(config=config, mode="dynamic")
        result = await analyze_behavior(
            request, sbom=sbom, policy=policy, controls=controls, llm_client=llm_client
        )

    mock_cls.assert_called_once_with(
        config=config, sbom=sbom, policy=policy, controls=controls, llm_client=llm_client
    )
    mock_instance.analyze.assert_awaited_once_with(mode="dynamic")
    assert result is sentinel_result


@pytest.mark.asyncio
async def test_analyze_behavior_propagates_exceptions():
    """A real analysis run is not best-effort — errors must not be swallowed."""
    with patch("nuguard.behavior.public_api.BehaviorAnalyzer") as mock_cls:
        mock_cls.return_value.analyze = AsyncMock(side_effect=RuntimeError("target down"))
        request = BehaviorAnalysisRequest(config=BehaviorConfig(target="http://localhost:9999"))
        with pytest.raises(RuntimeError, match="target down"):
            await analyze_behavior(request)


# ---------------------------------------------------------------------------
# run_behavior_scenarios — thin-wrapper parity with BehaviorRunner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_behavior_scenarios_constructs_runner_and_forwards_args():
    # A real (empty-findings) result rather than MagicMock(spec=...): the
    # wrapper now reads result.findings and sets result.remediation_plan for
    # remediation synthesis, and pydantic model classes don't expose field
    # names via dir(), so a spec'd mock can't stand in for those attributes.
    sentinel_result = BehaviorRunResult(run_id="run1")
    config = BehaviorConfig(target="http://localhost:9999")
    scenarios = [_scenario("a")]
    profile = DiscoveredProfile(customer_name="Bob", source="config")
    sbom = MagicMock()
    policy = MagicMock()
    intent = MagicMock()
    llm_client = MagicMock()
    judge_cache = MagicMock()

    with patch("nuguard.behavior.public_api.BehaviorRunner") as mock_cls:
        mock_instance = mock_cls.return_value
        mock_instance.run = AsyncMock(return_value=sentinel_result)

        request = BehaviorRunRequest(config=config, scenarios=scenarios, pre_scan_profile=profile)
        result = await run_behavior_scenarios(
            request,
            sbom=sbom,
            policy=policy,
            intent=intent,
            llm_client=llm_client,
            judge_cache=judge_cache,
        )

    mock_cls.assert_called_once_with(
        config=config, sbom=sbom, policy=policy, intent=intent, llm_client=llm_client, judge_cache=judge_cache
    )
    called_args, called_kwargs = mock_instance.run.await_args
    assert [s.name for s in called_args[0]] == ["a"]
    assert called_kwargs["pre_scan_profile"] is profile
    assert result is sentinel_result
    assert result.remediation_plan == []


@pytest.mark.asyncio
async def test_run_behavior_scenarios_populates_remediation_plan():
    """Findings + a real SBOM should produce structured RemediationArtefact objects."""
    from nuguard.behavior.models import RemediationArtefact, RemediationArtefactType

    finding = {
        "finding_id": "BA-004-1",
        "title": "PII disclosed via datastore",
        "description": "Agent leaked account_number in a response.",
        "affected_component": "SupportAgent",
        "severity": "high",
    }
    sentinel_result = BehaviorRunResult(run_id="run1", findings=[finding])
    config = BehaviorConfig(target="http://localhost:9999")

    fake_artefact = RemediationArtefact(
        finding_ids=["BA-004-1"],
        component="SupportAgent",
        component_type="AGENT",
        artefact_type=RemediationArtefactType.OUTPUT_GUARDRAIL,
        priority="high",
        rationale="Sensitive fields must not appear in agent responses.",
    )

    from types import SimpleNamespace

    empty_sbom = SimpleNamespace(nodes=[], edges=[])

    with (
        patch("nuguard.behavior.public_api.BehaviorRunner") as mock_runner_cls,
        patch("nuguard.behavior.remediation.RemediationSynthesizer.synthesize_findings_async") as mock_synth,
    ):
        mock_runner_cls.return_value.run = AsyncMock(return_value=sentinel_result)
        mock_synth.return_value = [fake_artefact]

        request = BehaviorRunRequest(config=config, scenarios=[_scenario("a")])
        result = await run_behavior_scenarios(request, sbom=empty_sbom)

    mock_synth.assert_awaited_once_with([finding])
    assert result.remediation_plan == [fake_artefact]


@pytest.mark.asyncio
async def test_run_behavior_scenarios_remediation_synthesis_failure_is_swallowed():
    """Remediation synthesis is best-effort — a failure must not fail the run."""
    sentinel_result = BehaviorRunResult(
        run_id="run1",
        findings=[{"finding_id": "f1", "title": "t", "description": "d", "affected_component": "c", "severity": "low"}],
    )
    config = BehaviorConfig(target="http://localhost:9999")

    from types import SimpleNamespace

    with (
        patch("nuguard.behavior.public_api.BehaviorRunner") as mock_runner_cls,
        patch(
            "nuguard.behavior.remediation.RemediationSynthesizer.synthesize_findings_async",
            side_effect=RuntimeError("boom"),
        ),
    ):
        mock_runner_cls.return_value.run = AsyncMock(return_value=sentinel_result)
        request = BehaviorRunRequest(config=config, scenarios=[_scenario("a")])
        result = await run_behavior_scenarios(request, sbom=SimpleNamespace(nodes=[], edges=[]))

    assert result.remediation_plan == []


# ---------------------------------------------------------------------------
# discover_behavior_profile — thin-wrapper parity with BehaviorRunner.discover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_behavior_profile_wraps_runner_discover():
    sentinel_profile = DiscoveredProfile(customer_name="Carol", source="live")
    config = BehaviorConfig(target="http://localhost:9999")

    with patch("nuguard.behavior.public_api.BehaviorRunner") as mock_cls:
        mock_instance = mock_cls.return_value
        mock_instance.discover = AsyncMock(return_value=sentinel_profile)

        result = await discover_behavior_profile(config)

    mock_cls.assert_called_once_with(config=config, sbom=None, policy=None, intent=None, llm_client=None)
    mock_instance.discover.assert_awaited_once_with()
    assert result is sentinel_profile


@pytest.mark.asyncio
async def test_discover_behavior_profile_returns_none_when_runner_finds_nothing():
    config = BehaviorConfig(target="http://localhost:9999")
    with patch("nuguard.behavior.public_api.BehaviorRunner") as mock_cls:
        mock_cls.return_value.discover = AsyncMock(return_value=None)
        result = await discover_behavior_profile(config)
    assert result is None
