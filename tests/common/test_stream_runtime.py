from __future__ import annotations

import asyncio

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


@pytest.mark.asyncio
async def test_stream_runtime_cancellation_emits_terminal_event_and_settles_result() -> None:
    started = asyncio.Event()

    async def _worker(controller) -> None:
        controller.publish(event_type="run_started", phase="init", payload={})
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            controller.publish_terminal(
                event_type="failed",
                phase="finalize",
                payload={"status": "failed", "failure_stage": "cancelled"},
            )
            controller.set_final_exception(exc)
            raise

    handle = create_stream_handle("run-cancel", _worker)
    await started.wait()
    handle.cancel()

    events = [event async for event in handle.events]
    with pytest.raises(asyncio.CancelledError):
        await handle.final_result()
    await handle.wait_closed()

    assert events[-1].event_type == "failed"
    assert events[-1].payload["failure_stage"] == "cancelled"


@pytest.mark.asyncio
async def test_stream_runtime_preserves_terminal_event_when_queue_is_full() -> None:
    async def _worker(controller) -> None:
        for _ in range(300):
            controller.publish(event_type="scenario_started", phase="execution", payload={})
        controller.publish_terminal(
            event_type="completed",
            phase="finalize",
            payload={"status": "completed"},
        )
        controller.set_final_result({"ok": True})

    handle = create_stream_handle("run-full", _worker)
    events = [event async for event in handle.events]

    assert events[-1].event_type == "completed"
    assert await handle.final_result() == {"ok": True}


@pytest.mark.asyncio
async def test_wait_closed_timeout_does_not_cancel_worker() -> None:
    release_worker = asyncio.Event()

    async def _worker(controller) -> None:
        await release_worker.wait()
        controller.publish_terminal(
            event_type="completed",
            phase="finalize",
            payload={"status": "completed"},
        )
        controller.set_final_result({"ok": True})

    handle = create_stream_handle("run-timeout", _worker)
    with pytest.raises(TimeoutError):
        await handle.wait_closed(timeout=0.001)

    release_worker.set()
    await handle.wait_closed(timeout=1)
    assert await handle.final_result() == {"ok": True}


@pytest.mark.asyncio
async def test_wait_closed_propagates_caller_cancellation() -> None:
    release_worker = asyncio.Event()

    async def _worker(controller) -> None:
        await release_worker.wait()
        controller.set_final_result({"ok": True})

    handle = create_stream_handle("run-wait-cancel", _worker)
    waiter = asyncio.create_task(handle.wait_closed())
    await asyncio.sleep(0)
    waiter.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter

    release_worker.set()
    await handle.wait_closed(timeout=1)


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


def test_redteam_progress_reducer_accepts_revised_plan_total() -> None:
    state = RedteamProgressState(run_id="r-escalation")
    events = [
        StreamEvent(
            event_type="scenario_plan_ready",
            run_id="r-escalation",
            sequence=1,
            phase="planning",
            payload={"scenarios_total": 2, "scenarios_completed": 0},
        ),
        StreamEvent(
            event_type="scenario_progress",
            run_id="r-escalation",
            sequence=2,
            phase="execution",
            payload={"scenarios_completed": 2},
        ),
        StreamEvent(
            event_type="scenario_plan_ready",
            run_id="r-escalation",
            sequence=3,
            phase="planning",
            payload={"scenarios_total": 4, "scenarios_completed": 2},
        ),
        StreamEvent(
            event_type="scenario_progress",
            run_id="r-escalation",
            sequence=4,
            phase="execution",
            payload={"scenarios_completed": 3},
        ),
    ]

    for event in events:
        state = apply_event_to_redteam_state(state, event)

    assert state.scenarios_total == 4
    assert state.scenarios_completed == 3
    assert state.progress_pct == 0.75


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
