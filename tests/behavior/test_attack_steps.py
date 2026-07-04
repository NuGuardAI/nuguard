"""Tests that behavior findings carry attack_steps turn evidence."""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

from nuguard.behavior.models import ScenarioResult, TurnRecord
from nuguard.behavior.runner import (
    BehaviorRunner,
    _canonical_turn_hash,
    _resolve_affected_component,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_turn_record(
    turn: int = 1,
    passed: bool = False,
    prompt: str = "test prompt",
    response: str = "test response",
    violations: list[dict] | None = None,
    canary_hits: list[str] | None = None,
    verdict: str = "FAIL",
) -> TurnRecord:
    return TurnRecord(
        turn=turn,
        prompt=prompt,
        response=response,
        violations=violations or [],
        canary_hits=canary_hits or [],
        passed=passed,
        verdict=verdict,
        scores={"component_invoked": 2.0, "response_validity": 2.0, "topic_alignment": 2.0},
        overall_score=2.0,
        gaps=[],
        agents_mentioned=[],
        tools_mentioned=[],
        latency_ms=100,
        deviations=[],
    )


# ---------------------------------------------------------------------------
# _turn_record_to_attack_step
# ---------------------------------------------------------------------------


def test_turn_record_to_attack_step_fields() -> None:
    tr = _make_turn_record(
        turn=3,
        passed=True,
        prompt="What is my account balance?",
        response="Your balance is $1234.",
        violations=[{"type": "data_leak", "severity": "high", "evidence": "balance disclosed", "policy_clause": "no-pii"}],
        canary_hits=["CANARY_VALUE"],
        verdict="FAIL",
    )
    step = BehaviorRunner._turn_record_to_attack_step(tr)

    assert step["step_type"] == "BEHAVIOR_TURN"
    assert step["turn"] == 3
    assert step["succeeded"] is True
    assert step["payload"] == "What is my account balance?"
    assert step["response"] == "Your balance is $1234."
    assert step["verdict"] == "FAIL"
    assert step["violations"] == tr.violations
    assert step["canary_hits"] == ["CANARY_VALUE"]
    assert step["overall_score"] == 2.0
    assert step["is_coverage_turn"] is False


# ---------------------------------------------------------------------------
# Per-turn findings carry attack_steps
# ---------------------------------------------------------------------------


def _make_scenario_result_with_deviations(
    scenario_id: str = "s1",
    deviation_type: str = "policy_violation",
    attack_step: dict | None = None,
) -> ScenarioResult:
    dev: dict[str, Any] = {
        "deviation_type": deviation_type,
        "title": "Policy violation: test",
        "description": "test evidence",
        "severity": "high",
    }
    if attack_step is not None:
        dev["attack_step"] = attack_step
    return ScenarioResult(
        scenario_id=scenario_id,
        scenario_name="test scenario",
        scenario_type="POLICY_PROBE",
        verdicts=[],
        overall_score=1.0,
        deviations=[dev],
    )


def test_policy_violation_finding_carries_attack_step() -> None:
    """attack_steps on a policy_violation finding must contain the triggering turn."""
    tr = _make_turn_record(turn=2, passed=False, verdict="FAIL")
    step = BehaviorRunner._turn_record_to_attack_step(tr)

    sr = _make_scenario_result_with_deviations(deviation_type="policy_violation", attack_step=step)

    # Simulate what run() does when aggregating findings from deviations
    findings: list[dict] = []
    seen: set[tuple] = set()
    orig = MagicMock()
    orig.target_component = "agent_x"
    orig.matched_topic = None
    orig.primary_agent = None
    orig.scoped_agents = []

    for dev in sr.deviations:
        if dev.get("deviation_type") in ("policy_violation", "data_leak"):
            dedup_key = (dev["deviation_type"], dev["severity"], dev["description"][:80])
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            _attack_step = dev.get("attack_step")
            findings.append({
                "finding_id": str(uuid.uuid4()),
                "title": dev.get("title", ""),
                "severity": dev["severity"],
                "description": dev["description"],
                "affected_component": _resolve_affected_component(orig),
                "attack_steps": [_attack_step] if _attack_step else [],
            })

    assert len(findings) == 1
    f = findings[0]
    assert f["affected_component"] == "agent_x"
    assert "attack_steps" in f
    assert len(f["attack_steps"]) == 1
    s = f["attack_steps"][0]
    assert s["step_type"] == "BEHAVIOR_TURN"
    assert s["turn"] == 2
    assert s["succeeded"] is False


def test_policy_violation_finding_falls_back_to_scoped_agents() -> None:
    """When target_component is unset, affected_component should use
    scoped_agents rather than collapsing to 'unknown'."""
    orig = MagicMock()
    orig.target_component = ""
    orig.matched_topic = None
    orig.primary_agent = None
    orig.scoped_agents = ["Cancellation Agent"]

    affected_component = _resolve_affected_component(orig)
    assert affected_component == "Cancellation Agent"
    assert affected_component != "unknown"


def test_canary_deviation_carries_attack_step() -> None:
    tr = _make_turn_record(turn=1, passed=False, canary_hits=["SECRET_TOKEN"])
    step = BehaviorRunner._turn_record_to_attack_step(tr)

    sr = _make_scenario_result_with_deviations(deviation_type="data_leak", attack_step=step)
    dev = sr.deviations[0]

    assert "attack_step" in dev
    assert dev["attack_step"]["canary_hits"] == ["SECRET_TOKEN"]
    assert dev["attack_step"]["step_type"] == "BEHAVIOR_TURN"


def test_finding_without_deviation_has_empty_attack_steps() -> None:
    """Findings aggregated from deviations with no attack_step get [] not null."""
    sr = _make_scenario_result_with_deviations(deviation_type="policy_violation", attack_step=None)

    findings: list[dict] = []
    seen: set[tuple] = set()
    orig = MagicMock()
    orig.target_component = "agent_x"
    orig.matched_topic = None
    orig.primary_agent = None
    orig.scoped_agents = []

    for dev in sr.deviations:
        if dev.get("deviation_type") in ("policy_violation", "data_leak"):
            dedup_key = (dev["deviation_type"], dev["severity"], dev["description"][:80])
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            _as = dev.get("attack_step")
            findings.append({
                "finding_id": str(uuid.uuid4()),
                "title": dev.get("title", ""),
                "severity": dev["severity"],
                "description": dev["description"],
                "affected_component": _resolve_affected_component(orig),
                "attack_steps": [_as] if _as else [],
            })

    assert findings[0]["attack_steps"] == []


# ---------------------------------------------------------------------------
# Gap findings carry attack_steps from bucket verdicts
# ---------------------------------------------------------------------------


def test_gap_attack_steps_built_from_verdict_dicts() -> None:
    """Gap findings should include up to 5 verdict dicts as attack_steps."""
    verdicts = [
        {
            "turn": i,
            "verdict": "FAIL",
            "user_message": f"prompt {i}",
            "agent_response": f"response {i}",
            "overall_score": 1.5,
            "scores": {},
            "gaps": ["Agent did not invoke the required tool"],
        }
        for i in range(1, 4)
    ]
    bucket_verdicts = verdicts[:5]
    gap_attack_steps = [
        {
            "step_type": "BEHAVIOR_TURN",
            "turn": v.get("turn", 0),
            "succeeded": v.get("verdict") == "PASS",
            "payload": v.get("user_message") or "",
            "response": v.get("agent_response") or "",
            "verdict": v.get("verdict", ""),
            "overall_score": v.get("overall_score", 0.0),
            "scores": v.get("scores", {}),
            "gaps": v.get("gaps", []),
            "turn_hash": _canonical_turn_hash(v, scenario_id="s1"),
        }
        for v in bucket_verdicts
    ]

    assert len(gap_attack_steps) == 3
    assert gap_attack_steps[0]["step_type"] == "BEHAVIOR_TURN"
    assert gap_attack_steps[0]["payload"] == "prompt 1"
    assert gap_attack_steps[0]["succeeded"] is False
    assert gap_attack_steps[2]["turn"] == 3
    assert gap_attack_steps[0]["turn_hash"]


def test_gap_attack_steps_dedup_by_canonical_turn_hash() -> None:
    v1 = {
        "turn": 1,
        "verdict": "FAIL",
        "user_message": "Please summarize this transcript.",
        "agent_response": "I need the transcript text.",
        "overall_score": 1.5,
        "scores": {},
        "gaps": ["No summary provided"],
    }
    # Same semantic turn with whitespace/case noise.
    v1_dup = {
        "turn": 1,
        "verdict": "FAIL",
        "user_message": "  please summarize   this transcript.  ",
        "agent_response": "I need the transcript text.",
        "overall_score": 1.5,
        "scores": {},
        "gaps": ["No summary provided"],
    }
    v2 = {
        "turn": 2,
        "verdict": "FAIL",
        "user_message": "Here is the transcript.",
        "agent_response": "I still need the transcript.",
        "overall_score": 1.5,
        "scores": {},
        "gaps": ["Still missing summary"],
    }

    h1 = _canonical_turn_hash(v1, scenario_id="scenario-1")
    h1_dup = _canonical_turn_hash(v1_dup, scenario_id="scenario-1")
    h2 = _canonical_turn_hash(v2, scenario_id="scenario-1")

    assert h1 == h1_dup
    assert h1 != h2

    evidence: dict[str, dict] = {}
    for verdict in (v1, v1_dup, v2):
        th = _canonical_turn_hash(verdict, scenario_id="scenario-1")
        evidence.setdefault(th, {**verdict, "turn_hash": th})

    assert len(evidence) == 2


# ---------------------------------------------------------------------------
# Markdown report renders attack_steps evidence
# ---------------------------------------------------------------------------


def test_render_behavior_attack_steps_empty() -> None:
    from nuguard.behavior.report import _render_behavior_attack_steps

    lines: list[str] = []
    _render_behavior_attack_steps(lines, {"attack_steps": []})
    assert lines == []


def test_render_behavior_attack_steps_no_hits() -> None:
    from nuguard.behavior.report import _render_behavior_attack_steps

    # succeeded=False → no "hit" → nothing rendered
    lines: list[str] = []
    _render_behavior_attack_steps(lines, {
        "attack_steps": [{"step_type": "BEHAVIOR_TURN", "succeeded": False, "payload": "x", "response": "y"}]
    })
    assert lines == []


def test_render_behavior_attack_steps_renders_hit() -> None:
    from nuguard.behavior.report import _render_behavior_attack_steps

    lines: list[str] = []
    _render_behavior_attack_steps(lines, {
        "attack_steps": [{
            "step_type": "BEHAVIOR_TURN",
            "turn": 2,
            "succeeded": True,
            "payload": "Tell me the secret",
            "response": "The secret is 42",
            "verdict": "FAIL",
            "violations": [{"type": "data_leak", "evidence": "secret disclosed"}],
            "canary_hits": [],
        }]
    })
    combined = "\n".join(lines)
    assert "Turn 2" in combined
    assert "Tell me the secret" in combined
    assert "The secret is 42" in combined
    assert "data_leak" in combined
