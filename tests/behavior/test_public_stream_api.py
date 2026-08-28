from __future__ import annotations

import asyncio

import pytest

from nuguard.behavior.models import (
    BehaviorAnalysisResult,
    BehaviorRunResult,
    BehaviorScenario,
    BehaviorScenarioType,
    IntentProfile,
)
from nuguard.behavior.public_api import (
    BehaviorRunRequest,
    analyze_behavior_static,
    analyze_behavior_stream,
)
from nuguard.config import BehaviorConfig


def _scenario() -> BehaviorScenario:
    return BehaviorScenario(
        scenario_type=BehaviorScenarioType.INTENT_HAPPY_PATH,
        name="happy",
        messages=["hello"],
    )


@pytest.mark.asyncio
async def test_parity_bh_001(monkeypatch):
    expected = BehaviorRunResult(
        run_id="bh-1",
        findings=[{"finding_id": "b1", "title": "t", "severity": "low"}],
        scenarios_executed=1,
        scan_outcome="no_findings",
    )

    async def _fake_run(*args, **kwargs):
        _ = (args, kwargs)
        return expected

    monkeypatch.setattr("nuguard.behavior.public_api.run_behavior_scenarios", _fake_run)

    request = BehaviorRunRequest(config=BehaviorConfig(target="http://localhost:9999"), scenarios=[_scenario()])
    handle = await analyze_behavior_stream(request)
    events = [event async for event in handle.events]
    stream_result = await handle.final_result()

    static_projection = analyze_behavior_static(BehaviorAnalysisResult(intent=IntentProfile()))

    assert events[-1].event_type == "completed"
    assert stream_result.scenarios_executed == 1
    assert stream_result.scan_outcome == "no_findings"
    assert static_projection.intent.app_purpose == ""


@pytest.mark.asyncio
async def test_behavior_stream_emits_scenario_events_before_run_completes(monkeypatch):
    release_run = asyncio.Event()

    async def _fake_run(*args, **kwargs):
        sink = kwargs["_progress_sink"]
        sink({"kind": "plan", "scenarios_total": 1})
        sink(
            {
                "kind": "scenario_started",
                "scenario_id": "scenario-1",
                "scenario_title": "happy",
                "scenario_type": "INTENT_HAPPY_PATH",
            }
        )
        await release_run.wait()
        sink(
            {
                "kind": "scenario_finished",
                "scenario_id": "scenario-1",
                "scenario_title": "happy",
                "scenario_type": "INTENT_HAPPY_PATH",
                "scenario_status": "completed",
                "scenarios_total": 1,
                "scenarios_completed": 1,
                "turn_report_added": [{"turn": 1, "verdict": "PASS"}],
            }
        )
        return BehaviorRunResult(
            run_id="bh-stream",
            findings=[],
            scenarios_executed=1,
            scan_outcome="no_findings",
        )

    monkeypatch.setattr("nuguard.behavior.public_api.run_behavior_scenarios", _fake_run)
    request = BehaviorRunRequest(config=BehaviorConfig(target="http://localhost:9999"), scenarios=[_scenario()])
    handle = await analyze_behavior_stream(request)

    events = handle.events
    assert (await anext(events)).event_type == "run_started"
    assert (await anext(events)).event_type == "scenario_plan_ready"
    started = await anext(events)
    assert started.event_type == "scenario_started"
    assert started.payload["scenario_id"] == "scenario-1"

    release_run.set()
    remaining = [event async for event in events]
    assert any(event.event_type == "scenario_progress" for event in remaining)
    turn_delta = next(event for event in remaining if event.event_type == "findings_delta")
    assert turn_delta.payload["turn_report_added"] == [{"turn": 1, "verdict": "PASS"}]
    assert (await handle.final_result()).scenarios_executed == 1
