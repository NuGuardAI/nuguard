"""Unit tests for nuguard.output.validation_report."""
from __future__ import annotations

from dataclasses import dataclass, field

from nuguard.output.validation_report import (
    ScenarioDetail,
    TurnDetail,
    _clean_response_for_display,
    extract_behavior_scenario_details,
    extract_redteam_scenario_details,
    render_behavior_coverage_evidence,
    render_scenario_details_section,
    render_validation_summary_bullets,
)


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------


@dataclass
class _ScenarioRecord:
    title: str
    goal_type: str
    scenario_type: str
    had_finding: bool
    chain_status: str = "completed"
    steps: list[dict] = field(default_factory=list)
    turns_used: int = 0
    turns_budget: int = 0
    duration_s: float = 0.0
    impact_score: float = 0.0


@dataclass
class _ScenarioResult:
    scenario_id: str
    scenario_name: str
    scenario_type: str
    overall_score: float = 4.0
    verdicts: list[dict] = field(default_factory=list)
    total_turns: int = 0
    coverage_turns: int = 0
    coverage_pct: float = 1.0
    deviations: list[dict] = field(default_factory=list)
    matched_topic: str | None = None
    uncovered_agents: list[str] = field(default_factory=list)
    uncovered_tools: list[str] = field(default_factory=list)


@dataclass
class _BehaviorCoverage:
    component_name: str
    node_type: str = "AGENT"
    exercised: bool = False
    exercised_within_policy: bool = False
    exercised_against_policy: bool = False
    deviations: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# extract_redteam_scenario_details
# ---------------------------------------------------------------------------


def test_extract_redteam_status_finding():
    r = _ScenarioRecord(title="Attack A", goal_type="DATA_EXFILTRATION", scenario_type="INJECT",
                        had_finding=True, chain_status="completed")
    details = extract_redteam_scenario_details([r])
    assert details[0].status == "FINDING"
    assert details[0].had_finding is True


def test_extract_redteam_status_pass():
    r = _ScenarioRecord(title="Safe", goal_type="POLICY_VIOLATION", scenario_type="PROBE",
                        had_finding=False, chain_status="completed")
    details = extract_redteam_scenario_details([r])
    assert details[0].status == "PASS"


def test_extract_redteam_status_aborted():
    r = _ScenarioRecord(title="Broken", goal_type="API_ATTACK", scenario_type="SCAN",
                        had_finding=False, chain_status="aborted")
    details = extract_redteam_scenario_details([r])
    assert details[0].status == "ABORTED"


def test_extract_redteam_turns_from_steps():
    steps = [
        {"step_type": "INJECT", "payload": "Hello", "response": "World", "succeeded": True},
        {"step_type": "PIVOT", "payload": "Next", "response": "Nope", "succeeded": False},
    ]
    r = _ScenarioRecord(title="T", goal_type="PROMPT_DRIVEN_THREAT", scenario_type="X",
                        had_finding=False, steps=steps)
    details = extract_redteam_scenario_details([r])
    assert len(details[0].turns) == 2
    assert details[0].turns[0].request == "Hello"
    assert details[0].turns[0].response == "World"
    assert details[0].turns[0].passed is True
    assert details[0].turns[1].passed is False


def test_extract_redteam_http_step_request_format():
    steps = [{"step_type": "INVOKE", "method": "GET", "target_path": "/api/data",
               "status_code": 200, "response": "ok", "succeeded": True}]
    r = _ScenarioRecord(title="T", goal_type="API_ATTACK", scenario_type="X",
                        had_finding=False, steps=steps)
    details = extract_redteam_scenario_details([r])
    assert details[0].turns[0].request == "GET /api/data → HTTP 200"


# ---------------------------------------------------------------------------
# extract_behavior_scenario_details
# ---------------------------------------------------------------------------


def test_extract_behavior_status_pass():
    sr = _ScenarioResult(scenario_id="1", scenario_name="happy", scenario_type="intent_happy_path",
                         overall_score=4.0)
    details = extract_behavior_scenario_details([sr])
    assert details[0].status == "PASS"


def test_extract_behavior_status_partial():
    sr = _ScenarioResult(scenario_id="1", scenario_name="partial", scenario_type="component_coverage",
                         overall_score=2.5)
    details = extract_behavior_scenario_details([sr])
    assert details[0].status == "PARTIAL"


def test_extract_behavior_status_fail():
    sr = _ScenarioResult(scenario_id="1", scenario_name="fail", scenario_type="invariant_probe",
                         overall_score=1.5)
    details = extract_behavior_scenario_details([sr])
    assert details[0].status == "FAIL"


def test_extract_behavior_turns_from_verdicts():
    verdicts = [
        {"turn": 1, "prompt": "ask something", "response": "answer", "verdict": "PASS", "scores": {}},
        {"turn": 2, "prompt": "ask more", "response": "no", "verdict": "FAIL", "scores": {}},
    ]
    sr = _ScenarioResult(scenario_id="1", scenario_name="s", scenario_type="agent_coverage",
                         overall_score=2.0, verdicts=verdicts, total_turns=2)
    details = extract_behavior_scenario_details([sr])
    assert len(details[0].turns) == 2
    assert details[0].turns[0].request == "ask something"
    assert details[0].turns[0].passed is True
    assert details[0].turns[1].passed is False


# ---------------------------------------------------------------------------
# render_validation_summary_bullets
# ---------------------------------------------------------------------------


def test_render_validation_summary_bullets_counts():
    lines: list[str] = []
    render_validation_summary_bullets(
        lines,
        total_scenarios=10,
        passed_scenarios=8,
        failed_scenarios=2,
        total_turns=45,
        type_breakdown={"PROMPT_DRIVEN_THREAT": 6, "DATA_EXFILTRATION": 4},
    )
    joined = "\n".join(lines)
    assert "**Total Scenarios**: 10" in joined
    assert "80%" in joined
    assert "8 passed" in joined
    assert "**Total Turns**: 45" in joined
    assert "Prompt Threat: 6" in joined
    assert "Data Exfil: 4" in joined


def test_render_validation_summary_bullets_zero_scenarios():
    lines: list[str] = []
    render_validation_summary_bullets(
        lines,
        total_scenarios=0,
        passed_scenarios=0,
        failed_scenarios=0,
        total_turns=0,
        type_breakdown={},
    )
    joined = "\n".join(lines)
    assert "**Total Scenarios**: 0" in joined
    assert "Success Rate" not in joined


# ---------------------------------------------------------------------------
# render_scenario_details_section
# ---------------------------------------------------------------------------


def test_render_scenario_details_section_structure():
    details = [
        ScenarioDetail(
            index=1, title="My Scenario", scenario_type="INJECT",
            goal_or_type="DATA_EXFILTRATION", status="FINDING",
            turns=[TurnDetail(turn_number=1, request="send this", response="got that", passed=True)],
            had_finding=True,
        )
    ]
    lines: list[str] = []
    render_scenario_details_section(lines, details)
    joined = "\n".join(lines)
    assert "## Scenario Details" in joined
    assert "### Scenario 1:" in joined
    assert "[FINDING]" in joined
    assert "#### Turn 1" in joined
    assert "send this" in joined
    assert "got that" in joined


def test_render_scenario_details_section_empty():
    lines: list[str] = []
    render_scenario_details_section(lines, [])
    assert lines == []


def test_render_scenario_details_truncates_long_text():
    long_text = "x" * 3000
    details = [
        ScenarioDetail(
            index=1, title="T", scenario_type="X", goal_or_type="Y", status="PASS",
            turns=[TurnDetail(turn_number=1, request=long_text, response="ok", passed=True)],
            had_finding=False,
        )
    ]
    lines: list[str] = []
    render_scenario_details_section(lines, details, truncate_limit=100)
    joined = "\n".join(lines)
    assert "truncated" in joined


# ---------------------------------------------------------------------------
# render_behavior_coverage_evidence
# ---------------------------------------------------------------------------


def test_render_behavior_coverage_evidence_exercised_component():
    verdicts = [{
        "turn": 1,
        "prompt": "Book a flight",
        "response": "Using booking_agent…",
        "verdict": "PASS",
        "agents_mentioned": ["booking_agent"],
        "tools_mentioned": [],
    }]
    sr = _ScenarioResult(scenario_id="1", scenario_name="booking_test",
                         scenario_type="agent_coverage", verdicts=verdicts,
                         matched_topic="flight booking")
    cov = _BehaviorCoverage(component_name="booking_agent", node_type="AGENT",
                             exercised=True, exercised_within_policy=True)
    lines: list[str] = []
    render_behavior_coverage_evidence(lines, [cov], [sr])
    joined = "\n".join(lines)
    assert "## Coverage Evidence" in joined
    assert "booking_agent" in joined
    assert "Within policy" in joined
    assert "booking_test" in joined
    assert "#### Evidence: booking_agent" in joined


def test_render_behavior_coverage_evidence_not_exercised():
    sr = _ScenarioResult(scenario_id="1", scenario_name="s", scenario_type="agent_coverage")
    cov = _BehaviorCoverage(component_name="payment_tool", node_type="TOOL", exercised=False)
    lines: list[str] = []
    render_behavior_coverage_evidence(lines, [cov], [sr])
    joined = "\n".join(lines)
    assert "Not exercised" in joined
    assert "payment_tool" in joined


# ---------------------------------------------------------------------------
# _clean_response_for_display
# ---------------------------------------------------------------------------


def test_clean_response_plain_text_unchanged():
    assert _clean_response_for_display("Hello, I can help you.") == "Hello, I can help you."


def test_clean_response_chat_endpoint_extracts_last_message():
    payload = '{"conversation_id":"abc","messages":[{"content":"Hi there!","agent":"Bot"}],"events":[]}'
    result = _clean_response_for_display(payload)
    assert result == "Hi there!"
    assert "conversation_id" not in result


def test_clean_response_chat_endpoint_picks_last_message():
    payload = '{"messages":[{"content":"First"},{"content":"Second"}]}'
    assert _clean_response_for_display(payload) == "Second"


def test_clean_response_api_json_formats_as_code_block():
    payload = '{"account_number":"11111","name":"Alice"}'
    result = _clean_response_for_display(payload)
    assert result.startswith("```json")
    assert "account_number" in result
    assert result.endswith("```")


def test_clean_response_invalid_json_unchanged():
    bad = '{"not": "closed'
    assert _clean_response_for_display(bad) == bad


def test_clean_response_chat_with_empty_content_falls_back_to_code_block():
    payload = '{"messages":[{"content":""}]}'
    result = _clean_response_for_display(payload)
    assert result.startswith("```json")


def test_render_behavior_coverage_evidence_matched_topic():
    sr = _ScenarioResult(scenario_id="1", scenario_name="refund_test",
                         scenario_type="intent_happy_path", matched_topic="refunds")
    lines: list[str] = []
    render_behavior_coverage_evidence(lines, [], [sr])
    joined = "\n".join(lines)
    assert "refunds" in joined
    assert "refund_test" in joined
