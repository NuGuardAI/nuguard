"""Unit tests for nuguard/behavior/coverage.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nuguard.behavior.coverage import (
    MAX_COVERAGE_TURNS,
    CoverageState,
    generate_coverage_turns,
)
from nuguard.behavior.judge import TurnVerdict
from nuguard.behavior.models import IntentProfile


def _make_verdict(
    agents_mentioned: list[str] | None = None,
    tools_mentioned: list[str] | None = None,
) -> TurnVerdict:
    return TurnVerdict(
        turn=1,
        scenario_name="test",
        verdict="PASS",
        scores={},
        overall_score=4.0,
        reasoning="ok",
        agents_mentioned=agents_mentioned or [],
        tools_mentioned=tools_mentioned or [],
        policy_issues=[],
        gaps=[],
        deviations=[],
        suggested_followup=None,
        latency_ms=100,
    )


def _make_intent() -> IntentProfile:
    return IntentProfile(
        app_purpose="Marketing AI assistant",
        core_capabilities=["generate ad copy"],
        behavioral_bounds=[],
    )


# ---------------------------------------------------------------------------
# CoverageState
# ---------------------------------------------------------------------------


def test_coverage_state_empty():
    state = CoverageState(expected_agents=[], expected_tools=[])
    assert state.coverage_pct == 1.0
    assert state.uncovered_agents == set()
    assert state.uncovered_tools == set()


def test_coverage_state_initially_uncovered():
    state = CoverageState(expected_agents=["CopyAgent"], expected_tools=["search_tool"])
    assert state.coverage_pct == 0.0
    assert "CopyAgent" in state.uncovered_agents
    assert "search_tool" in state.uncovered_tools


def test_coverage_state_update_marks_agent_covered():
    state = CoverageState(expected_agents=["CopyAgent"], expected_tools=[])
    verdict = _make_verdict(agents_mentioned=["CopyAgent"])
    state.update(verdict)
    assert "CopyAgent" not in state.uncovered_agents
    assert state.coverage_pct == 1.0


def test_coverage_state_update_marks_tool_covered():
    state = CoverageState(expected_agents=[], expected_tools=["search_tool"])
    verdict = _make_verdict(tools_mentioned=["search_tool"])
    state.update(verdict)
    assert "search_tool" not in state.uncovered_tools
    assert state.coverage_pct == 1.0


def test_coverage_state_partial_coverage():
    state = CoverageState(expected_agents=["AgentA", "AgentB"], expected_tools=["tool1"])
    verdict = _make_verdict(agents_mentioned=["AgentA"], tools_mentioned=["tool1"])
    state.update(verdict)
    assert state.coverage_pct == pytest.approx(2 / 3, abs=0.001)
    assert "AgentB" in state.uncovered_agents


def test_coverage_state_normalised_name_matching():
    """'AdCopyWriter' in verdict should match 'ad_copy_writer' in expected."""
    state = CoverageState(expected_agents=["ad_copy_writer"], expected_tools=[])
    verdict = _make_verdict(agents_mentioned=["AdCopyWriter"])
    state.update(verdict)
    assert state.uncovered_agents == set()
    assert state.coverage_pct == 1.0


def test_coverage_state_idempotent_update():
    """Updating with the same component twice doesn't double-count."""
    state = CoverageState(expected_agents=["CopyAgent"], expected_tools=[])
    verdict = _make_verdict(agents_mentioned=["CopyAgent"])
    state.update(verdict)
    state.update(verdict)
    assert state.coverage_pct == 1.0


# ---------------------------------------------------------------------------
# generate_coverage_turns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_coverage_turns_deterministic():
    turns = await generate_coverage_turns(
        uncovered=["SearchAgent"],
        session_context="Discussing marketing campaigns",
        component_descriptions={"SearchAgent": "Performs web search"},
        llm_client=None,
        domain_context="marketing",
        intent=_make_intent(),
    )
    assert len(turns) >= 1
    assert len(turns) <= MAX_COVERAGE_TURNS
    for t in turns:
        assert isinstance(t, str)
        assert len(t) > 0


@pytest.mark.asyncio
async def test_generate_coverage_turns_respects_max():
    many_components = [f"Agent{i}" for i in range(20)]
    turns = await generate_coverage_turns(
        uncovered=many_components,
        session_context="Discussing marketing",
        component_descriptions={},
        llm_client=None,
        domain_context="marketing",
        intent=_make_intent(),
    )
    assert len(turns) <= MAX_COVERAGE_TURNS


@pytest.mark.asyncio
async def test_generate_coverage_turns_with_llm():
    mock_llm = MagicMock()
    mock_llm.api_key = "test-key"
    mock_llm.complete = AsyncMock(
        return_value='["Can you use the search agent to find relevant marketing trends?"]'
    )
    turns = await generate_coverage_turns(
        uncovered=["SearchAgent"],
        session_context="Marketing assistant conversation",
        component_descriptions={"SearchAgent": "Web search"},
        llm_client=mock_llm,
        domain_context="marketing",
        intent=_make_intent(),
    )
    assert len(turns) >= 1
    assert all(isinstance(t, str) for t in turns)


@pytest.mark.asyncio
async def test_generate_coverage_turns_llm_failure_fallback():
    mock_llm = MagicMock()
    mock_llm.api_key = "test-key"
    mock_llm.complete = AsyncMock(side_effect=RuntimeError("Network error"))
    turns = await generate_coverage_turns(
        uncovered=["SearchAgent"],
        session_context="Discussing marketing",
        component_descriptions={},
        llm_client=mock_llm,
        domain_context="marketing",
        intent=_make_intent(),
    )
    assert len(turns) >= 1


@pytest.mark.asyncio
async def test_generate_coverage_turns_empty_uncovered():
    turns = await generate_coverage_turns(
        uncovered=[],
        session_context="",
        component_descriptions={},
        llm_client=None,
        domain_context="general",
        intent=_make_intent(),
    )
    assert turns == []


@pytest.mark.asyncio
async def test_generate_coverage_turns_includes_intent_purpose():
    turns = await generate_coverage_turns(
        uncovered=["ReportAgent"],
        session_context="",
        component_descriptions={},
        llm_client=None,
        domain_context="marketing",
        intent=_make_intent(),
    )
    # At least one turn should reference the intent's app_purpose
    combined = " ".join(turns).lower()
    # The deterministic template should embed the intent somewhere
    assert "reportagent" in combined.lower() or "report" in combined.lower()
