from __future__ import annotations

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
