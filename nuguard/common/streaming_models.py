"""Shared Pydantic contracts for streaming public APIs.

This module defines event envelopes and deterministic derived-state models used
by redteam/behavior streaming entrypoints.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

STREAM_SCHEMA_VERSION = "1.0"

StreamEventType = Literal[
    "run_started",
    "scenario_plan_ready",
    "scenario_progress",
    "findings_delta",
    "heartbeat",
    "completed",
    "failed",
]


class StreamProgressPayload(BaseModel):
    """Progress snapshot emitted during long-running scans."""

    scenarios_total: int = 0
    scenarios_completed: int = 0
    progress_pct: float = 0.0
    eta_seconds: int | None = None
    current_goal_type: str | None = None
    current_scenario_type: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StreamDeltaPayload(BaseModel):
    """Incremental payload for findings and records."""

    findings_added: list[dict[str, Any]] = Field(default_factory=list)
    turn_report_added: list[dict[str, Any]] = Field(default_factory=list)
    scenario_record_added: list[dict[str, Any]] = Field(default_factory=list)


class StreamTerminalPayload(BaseModel):
    """Terminal event payload for completed/failed outcomes."""

    status: Literal["completed", "failed"]
    summary: dict[str, Any] = Field(default_factory=dict)
    is_retryable: bool = False
    failure_stage: str | None = None
    error_type: str | None = None
    error_message: str | None = None


class StreamEvent(BaseModel):
    """Common stream event envelope."""

    event_type: StreamEventType
    run_id: str
    sequence: int
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    phase: str = "runtime"
    payload: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = STREAM_SCHEMA_VERSION
    correlation_id: str | None = None


class RedteamProgressState(BaseModel):
    """Deterministic, reduced progress state for redteam streams."""

    run_id: str
    schema_version: str = STREAM_SCHEMA_VERSION
    scenarios_total: int = 0
    scenarios_completed: int = 0
    findings_count: int = 0
    scenario_record_count: int = 0
    progress_pct: float = 0.0
    terminal_status: Literal["running", "completed", "failed"] = "running"


class BehaviorProgressState(BaseModel):
    """Deterministic, reduced progress state for behavior streams."""

    run_id: str
    schema_version: str = STREAM_SCHEMA_VERSION
    scenarios_total: int = 0
    scenarios_completed: int = 0
    findings_count: int = 0
    turn_report_count: int = 0
    progress_pct: float = 0.0
    terminal_status: Literal["running", "completed", "failed"] = "running"


def _pct(completed: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(min(max(completed, 0) / total, 1.0), 6)


def apply_event_to_redteam_state(state: RedteamProgressState, event: StreamEvent) -> RedteamProgressState:
    """Apply one event to the redteam reduced state deterministically."""
    if event.event_type == "scenario_plan_ready":
        total = int(event.payload.get("scenarios_total") or 0)
        if state.scenarios_total == 0 and total > 0:
            state.scenarios_total = total
            state.progress_pct = _pct(state.scenarios_completed, state.scenarios_total)
    elif event.event_type == "scenario_progress":
        completed = int(event.payload.get("scenarios_completed") or state.scenarios_completed)
        state.scenarios_completed = max(state.scenarios_completed, completed)
        state.progress_pct = _pct(state.scenarios_completed, state.scenarios_total)
    elif event.event_type == "findings_delta":
        delta = StreamDeltaPayload.model_validate(event.payload)
        state.findings_count += len(delta.findings_added)
        state.scenario_record_count += len(delta.scenario_record_added)
    elif event.event_type == "completed":
        state.terminal_status = "completed"
        state.progress_pct = 1.0 if state.scenarios_total > 0 else state.progress_pct
    elif event.event_type == "failed":
        state.terminal_status = "failed"
    return state


def apply_event_to_behavior_state(state: BehaviorProgressState, event: StreamEvent) -> BehaviorProgressState:
    """Apply one event to the behavior reduced state deterministically."""
    if event.event_type == "scenario_plan_ready":
        total = int(event.payload.get("scenarios_total") or 0)
        if state.scenarios_total == 0 and total > 0:
            state.scenarios_total = total
            state.progress_pct = _pct(state.scenarios_completed, state.scenarios_total)
    elif event.event_type == "scenario_progress":
        completed = int(event.payload.get("scenarios_completed") or state.scenarios_completed)
        state.scenarios_completed = max(state.scenarios_completed, completed)
        state.progress_pct = _pct(state.scenarios_completed, state.scenarios_total)
    elif event.event_type == "findings_delta":
        delta = StreamDeltaPayload.model_validate(event.payload)
        state.findings_count += len(delta.findings_added)
        state.turn_report_count += len(delta.turn_report_added)
    elif event.event_type == "completed":
        state.terminal_status = "completed"
        state.progress_pct = 1.0 if state.scenarios_total > 0 else state.progress_pct
    elif event.event_type == "failed":
        state.terminal_status = "failed"
    return state
