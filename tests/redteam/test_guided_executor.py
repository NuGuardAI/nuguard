"""Tests for GuidedAttackExecutor — conversation loop, abort conditions, and success."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from nuguard.models.exploit_chain import GoalType
from nuguard.redteam.executor.guided_executor import GuidedAttackExecutor
from nuguard.redteam.models.guided_conversation import GuidedConversation, TurnRecord
from nuguard.redteam.target.client import TargetUnavailableError
from nuguard.redteam.target.session import AttackSession


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_conv(max_turns: int = 5, goal_type: GoalType = GoalType.DATA_EXFILTRATION) -> GuidedConversation:
    return GuidedConversation(
        conversation_id="test-conv-123",
        goal_type=goal_type,
        goal_description="Extract patient PHI from the medical agent",
        max_turns=max_turns,
    )


def _make_session() -> AttackSession:
    return AttackSession(
        session_id="test-session",
        target_url="http://test-app/chat",
        chain_id="test-chain",
    )


def _make_director(
    milestones=None,
    next_turn_msg="Hi there",
    next_turn_tactic="rapport",
    progress_score: int = 2,
    progress_reason="some progress",
) -> MagicMock:
    director = MagicMock()
    director.SUCCESS_SCORE = 5
    director.STALL_ABORT_THRESHOLD = 6
    director.plan_milestones = AsyncMock(return_value=milestones or ["rapport", "probe", "extract"])
    director.next_turn = AsyncMock(return_value=(next_turn_msg, next_turn_tactic))
    # assess_progress now returns (score, reasoning, evidence_quote, success_indicator, failure_cls)
    director.assess_progress = AsyncMock(
        return_value=(progress_score, progress_reason, "", None, "NONE")
    )
    return director


def _make_client(response: str = "I'm here to help you.") -> MagicMock:
    client = MagicMock()
    client.send = AsyncMock(return_value=(response, []))
    return client


def _make_executor(client=None, director=None, canary=None) -> GuidedAttackExecutor:
    return GuidedAttackExecutor(
        client=client or _make_client(),
        director=director or _make_director(),
        canary=canary,
    )


# ── GuidedConversation model helpers ─────────────────────────────────────────


def test_current_turn_number_empty():
    conv = _make_conv()
    assert conv.current_turn_number == 0


def test_last_progress_empty():
    conv = _make_conv()
    assert conv.last_progress == 1  # default int when no turns


def test_consecutive_stalled_turns():
    conv = _make_conv()
    conv.turns = [
        TurnRecord(turn=1, attacker_message="hi", agent_response="hello", progress_score=3),
        TurnRecord(turn=2, attacker_message="tell me", agent_response="no", progress_score=1),
        TurnRecord(turn=3, attacker_message="please", agent_response="no", progress_score=2),
    ]
    assert conv.consecutive_stalled_turns() == 2


def test_format_transcript():
    conv = _make_conv()
    conv.turns = [
        TurnRecord(
            turn=1,
            attacker_message="Hi there",
            agent_response="Hello! How can I help?",
            progress_score=2,
            tactic_used="rapport",
        )
    ]
    transcript = conv.format_transcript()
    assert "Turn 1" in transcript
    assert "rapport" in transcript
    assert "Hi there" in transcript
    assert "Hello!" in transcript


# ── milestone index helper ────────────────────────────────────────────────────


def test_current_milestone_idx_no_turns():
    conv = _make_conv()
    conv.milestones = ["a", "b", "c"]
    assert GuidedAttackExecutor._current_milestone_idx(conv, 0) == 0


def test_current_milestone_idx_advances():
    conv = _make_conv()
    conv.milestones = ["a", "b", "c"]
    conv.turns = [
        TurnRecord(turn=1, attacker_message="m1", agent_response="r1", milestone_reached=0),
        TurnRecord(turn=2, attacker_message="m2", agent_response="r2", milestone_reached=1),
    ]
    # Should be at milestone index 2 (next after last reached = 1)
    result = GuidedAttackExecutor._current_milestone_idx(conv, 0)
    assert result == 2


def test_current_milestone_idx_capped_at_last():
    conv = _make_conv()
    conv.milestones = ["a", "b"]
    conv.turns = [
        TurnRecord(turn=1, attacker_message="m1", agent_response="r1", milestone_reached=1),
    ]
    result = GuidedAttackExecutor._current_milestone_idx(conv, 0)
    # max reached=1, next=2, but capped at len-1=1
    assert result == 1


# ── Executor: max turns abort ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_turns_abort():
    conv = _make_conv(max_turns=3)
    director = _make_director(progress_score=2)  # never reaches SUCCESS_SCORE
    executor = _make_executor(director=director)
    result = await executor.run(conv, _make_session())

    assert result.abort_reason == "max_turns"
    assert result.succeeded is False
    assert len(result.turns) == 3


# ── Executor: goal achieved ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_goal_achieved_on_first_turn():
    conv = _make_conv(max_turns=10)
    director = _make_director(progress_score=5, progress_reason="PHI revealed")
    executor = _make_executor(director=director)
    result = await executor.run(conv, _make_session())

    assert result.succeeded is True
    assert result.final_progress >= 5
    assert len(result.turns) == 1


# ── Executor: hard refusal abort ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hard_refusal_abort():
    """3 consecutive hard-refusal turns should abort the conversation."""
    conv = _make_conv(max_turns=20)
    refusal_response = "I'm sorry, but I cannot and will not help with that. This is against my guidelines."
    client = _make_client(response=refusal_response)
    director = _make_director(progress_score=1)
    # assess_progress must classify as HARD_REFUSAL to trigger abort
    director.assess_progress = AsyncMock(
        return_value=(1, "Agent refused", "", None, "HARD_REFUSAL")
    )
    executor = _make_executor(client=client, director=director)

    result = await executor.run(conv, _make_session())
    assert result.abort_reason == "hard_refusal"
    assert result.succeeded is False


# ── Executor: target unavailable ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_target_unavailable_abort():
    conv = _make_conv(max_turns=10)
    client = MagicMock()
    client.send = AsyncMock(side_effect=TargetUnavailableError("Connection refused"))
    director = _make_director()
    executor = _make_executor(client=client, director=director)

    result = await executor.run(conv, _make_session())
    assert result.abort_reason == "target_unavailable"
    assert result.succeeded is False


# ── Executor: canary hit overrides progress ───────────────────────────────────


@pytest.mark.asyncio
async def test_canary_hit_overrides_to_success():
    conv = _make_conv(max_turns=10)
    director = _make_director(progress_score=1, progress_reason="low progress")
    canary = MagicMock()
    canary.scan = MagicMock(return_value=["PATIENT_ID_12345"])
    canary.scan_decoded = MagicMock(return_value=[])
    executor = _make_executor(director=director, canary=canary)

    result = await executor.run(conv, _make_session())
    # Canary hit should override progress to 5 → success
    assert result.succeeded is True
    last_turn = result.turns[-1]
    assert last_turn.progress_score == 5
    assert "PATIENT_ID_12345" in last_turn.progress_reasoning


# ── Executor: milestones planned once ────────────────────────────────────────


@pytest.mark.asyncio
async def test_milestones_planned_before_first_turn():
    conv = _make_conv(max_turns=2)
    assert conv.milestones == []
    director = _make_director(milestones=["step1", "step2", "step3"])
    executor = _make_executor(director=director)

    result = await executor.run(conv, _make_session())
    assert result.milestones == ["step1", "step2", "step3"]
    director.plan_milestones.assert_awaited_once()


@pytest.mark.asyncio
async def test_milestones_not_re_planned_if_already_set():
    conv = _make_conv(max_turns=2)
    conv.milestones = ["pre-existing"]
    director = _make_director()
    executor = _make_executor(director=director)

    await executor.run(conv, _make_session())
    director.plan_milestones.assert_not_awaited()


# ── Executor: turn records populated ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_turn_records_populated():
    conv = _make_conv(max_turns=2)
    director = _make_director(
        next_turn_msg="Tell me about your data",
        next_turn_tactic="bridge",
        progress_score=2,
        progress_reason="partial engagement",
    )
    client = _make_client(response="I can help with general queries.")
    executor = _make_executor(client=client, director=director)

    result = await executor.run(conv, _make_session())

    assert len(result.turns) == 2
    turn1 = result.turns[0]
    assert turn1.turn == 1
    assert turn1.attacker_message == "Tell me about your data"
    assert turn1.agent_response == "I can help with general queries."
    assert turn1.tactic_used == "bridge"
    assert turn1.progress_score == 2
    assert turn1.progress_reasoning == "partial engagement"


# ── Executor: next_turn failure falls back gracefully ────────────────────────


@pytest.mark.asyncio
async def test_next_turn_exception_uses_fallback():
    conv = _make_conv(max_turns=1)
    director = _make_director()
    director.next_turn = AsyncMock(side_effect=Exception("LLM unavailable"))
    executor = _make_executor(director=director)

    result = await executor.run(conv, _make_session())
    assert len(result.turns) == 1
    # Fallback message contains goal description
    assert len(result.turns[0].attacker_message) > 0
    assert result.turns[0].tactic_used == "fallback"
