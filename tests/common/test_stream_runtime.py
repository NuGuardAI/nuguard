from __future__ import annotations

import pytest

from nuguard.common.stream_runtime import create_stream_handle
from nuguard.common.streaming_models import (
    BehaviorProgressState,
    RedteamProgressState,
    StreamEvent,
    apply_event_to_behavior_state,
    apply_event_to_redteam_state,
)


@pytest.mark.asyncio
async def test_stream_runtime_sequences_events_and_completes() -> None:
    async def _worker(controller) -> None:
        controller.publish(event_type="run_started", phase="init", payload={})
        controller.publish(event_type="scenario_progress", phase="execution", payload={"scenarios_completed": 1})
        controller.publish_terminal(event_type="completed", phase="finalize", payload={"status": "completed"})
        controller.set_final_result({"ok": True})

    handle = create_stream_handle("run-1", _worker)

    events = [event async for event in handle.events]
    result = await handle.final_result()

    assert [e.sequence for e in events] == [1, 2, 3]
    assert [e.event_type for e in events] == ["run_started", "scenario_progress", "completed"]
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_stream_runtime_ignores_post_terminal_events() -> None:
    async def _worker(controller) -> None:
        controller.publish(event_type="run_started", phase="init", payload={})
        controller.publish_terminal(event_type="completed", phase="finalize", payload={"status": "completed"})
        controller.publish(event_type="heartbeat", phase="execution", payload={})
        controller.set_final_result({"ok": True})

    handle = create_stream_handle("run-2", _worker)
    events = [event async for event in handle.events]

    assert [e.event_type for e in events] == ["run_started", "completed"]


def test_redteam_progress_reducer_is_deterministic() -> None:
    events = [
        StreamEvent(event_type="run_started", run_id="r1", sequence=1, phase="init", payload={}),
        StreamEvent(
            event_type="scenario_plan_ready",
            run_id="r1",
            sequence=2,
            phase="planning",
            payload={"scenarios_total": 2},
        ),
        StreamEvent(
            event_type="scenario_progress",
            run_id="r1",
            sequence=3,
            phase="execution",
            payload={"scenarios_completed": 1},
        ),
        StreamEvent(
            event_type="findings_delta",
            run_id="r1",
            sequence=4,
            phase="execution",
            payload={"findings_added": [{"finding_id": "f1"}], "scenario_record_added": [{"id": "s1"}]},
        ),
        StreamEvent(event_type="completed", run_id="r1", sequence=5, phase="finalize", payload={}),
    ]

    state1 = RedteamProgressState(run_id="r1")
    for event in events:
        state1 = apply_event_to_redteam_state(state1, event)

    state2 = RedteamProgressState(run_id="r1")
    for event in events:
        state2 = apply_event_to_redteam_state(state2, event)

    assert state1.model_dump(mode="json") == state2.model_dump(mode="json")
    assert state1.terminal_status == "completed"
    assert state1.findings_count == 1
    assert state1.scenario_record_count == 1


def test_behavior_progress_reducer_is_deterministic() -> None:
    events = [
        StreamEvent(event_type="run_started", run_id="b1", sequence=1, phase="init", payload={}),
        StreamEvent(
            event_type="scenario_plan_ready",
            run_id="b1",
            sequence=2,
            phase="planning",
            payload={"scenarios_total": 3},
        ),
        StreamEvent(
            event_type="scenario_progress",
            run_id="b1",
            sequence=3,
            phase="execution",
            payload={"scenarios_completed": 2},
        ),
        StreamEvent(
            event_type="findings_delta",
            run_id="b1",
            sequence=4,
            phase="execution",
            payload={"findings_added": [{"finding_id": "f1"}], "turn_report_added": [{"turn": 1}]},
        ),
        StreamEvent(event_type="failed", run_id="b1", sequence=5, phase="finalize", payload={}),
    ]

    state1 = BehaviorProgressState(run_id="b1")
    for event in events:
        state1 = apply_event_to_behavior_state(state1, event)

    state2 = BehaviorProgressState(run_id="b1")
    for event in events:
        state2 = apply_event_to_behavior_state(state2, event)

    assert state1.model_dump(mode="json") == state2.model_dump(mode="json")
    assert state1.terminal_status == "failed"
    assert state1.findings_count == 1
    assert state1.turn_report_count == 1
