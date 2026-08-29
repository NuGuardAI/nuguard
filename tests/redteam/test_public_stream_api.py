from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from nuguard.models.finding import Finding, Severity
from nuguard.models.token_usage import TokenUsage
from nuguard.redteam.public_api import RedteamRunRequest, RedteamRunResult, run_redteam_stream


def _finding() -> Finding:
    return Finding(
        finding_id="rt-1",
        title="Prompt injection",
        severity=Severity.HIGH,
        description="desc",
        goal_type="prompt_driven_threat",
        scenario_type="PROMPT_INJECTION",
    )


@pytest.mark.asyncio
async def test_parity_rt_001(monkeypatch):
    expected = RedteamRunResult(
        findings=[_finding()],
        scenario_records=[{"title": "s1"}],
        scan_outcome="high_findings",
        token_usage=TokenUsage(input_tokens=2, output_tokens=3),
        resolved_chat_path="/chat",
        resolved_chat_path_source="config",
        scenarios_run=1,
    )

    async def _fake_run(*args, **kwargs):
        _ = (args, kwargs)
        return expected

    monkeypatch.setattr("nuguard.redteam.public_api.run_redteam", _fake_run)

    handle = await run_redteam_stream(RedteamRunRequest(target_url="http://target"), sbom=MagicMock())
    events = [event async for event in handle.events]
    result = await handle.final_result()

    assert events[-1].event_type == "completed"
    assert result.scan_outcome == "high_findings"
    assert result.scenarios_run == 1
    assert len(result.findings) == 1


@pytest.mark.asyncio
async def test_redteam_stream_emits_scenario_events_before_run_completes(monkeypatch):
    release_run = asyncio.Event()

    async def _fake_run(*args, **kwargs):
        sink = kwargs["_progress_sink"]
        sink({"kind": "plan", "scenarios_total": 1})
        sink(
            {
                "kind": "scenario_started",
                "scenario_id": "scenario-1",
                "scenario_title": "Prompt injection",
                "scenario_type": "PROMPT_INJECTION",
                "goal_type": "PROMPT_DRIVEN_THREAT",
            }
        )
        await release_run.wait()
        sink(
            {
                "kind": "scenario_finished",
                "scenario_id": "scenario-1",
                "scenario_title": "Prompt injection",
                "scenario_type": "PROMPT_INJECTION",
                "goal_type": "PROMPT_DRIVEN_THREAT",
                "scenario_status": "completed",
                "scenarios_total": 1,
                "scenarios_completed": 1,
                "findings_added": [_finding()],
            }
        )
        return RedteamRunResult(
            findings=[],
            scenario_records=[],
            scan_outcome="no_findings",
            token_usage=TokenUsage(),
            resolved_chat_path="/chat",
            resolved_chat_path_source="config",
            scenarios_run=1,
        )

    monkeypatch.setattr("nuguard.redteam.public_api.run_redteam", _fake_run)
    handle = await run_redteam_stream(RedteamRunRequest(target_url="http://target"), sbom=MagicMock())

    events = handle.events
    assert (await anext(events)).event_type == "run_started"
    assert (await anext(events)).event_type == "scenario_plan_ready"
    started = await anext(events)
    assert started.event_type == "scenario_started"
    assert started.payload["scenario_id"] == "scenario-1"

    release_run.set()
    remaining = [event async for event in events]
    assert any(event.event_type == "scenario_progress" for event in remaining)
    finding_delta = next(event for event in remaining if event.event_type == "findings_delta")
    assert finding_delta.payload["findings_added"][0]["finding_id"] == "rt-1"
    assert (await handle.final_result()).scenarios_run == 1
