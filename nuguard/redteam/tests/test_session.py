"""Unit tests for nuguard.redteam.target.session.AttackSession and TurnRecord."""
from __future__ import annotations

import pytest

from nuguard.redteam.target.session import AttackSession, TurnRecord


def _make_session() -> AttackSession:
    return AttackSession(
        session_id="s1",
        target_url="http://localhost",
        chain_id="c1",
    )


def test_add_turn_appends_turn_record() -> None:
    session = _make_session()
    record = session.add_turn("hello", "world")
    assert isinstance(record, TurnRecord)
    assert len(session.turns) == 1


def test_add_turn_returns_turn_record() -> None:
    session = _make_session()
    record = session.add_turn("prompt", "response")
    assert record.prompt == "prompt"
    assert record.response == "response"


def test_add_turn_increments_turn_counter() -> None:
    session = _make_session()
    r1 = session.add_turn("p1", "r1")
    r2 = session.add_turn("p2", "r2")
    r3 = session.add_turn("p3", "r3")
    assert r1.turn == 1
    assert r2.turn == 2
    assert r3.turn == 3


def test_add_evidence_stores_by_step_id() -> None:
    session = _make_session()
    session.add_evidence("step-1", "some response text")
    assert session.evidence["step-1"] == "some response text"


def test_last_response_returns_most_recent() -> None:
    session = _make_session()
    session.add_turn("p1", "first response")
    session.add_turn("p2", "second response")
    assert session.last_response == "second response"


def test_last_response_empty_on_no_turns() -> None:
    session = _make_session()
    assert session.last_response == ""


def test_all_tool_calls_aggregates_across_turns() -> None:
    session = _make_session()
    tc1 = {"name": "tool_a", "args": {}}
    tc2 = {"name": "tool_b", "args": {}}
    session.add_turn("p1", "r1", tool_calls=[tc1])
    session.add_turn("p2", "r2", tool_calls=[tc2])
    all_calls = session.all_tool_calls
    assert tc1 in all_calls
    assert tc2 in all_calls
    assert len(all_calls) == 2


def test_all_tool_calls_empty_on_no_turns() -> None:
    session = _make_session()
    assert session.all_tool_calls == []


def test_all_tool_calls_collects_from_each_turn() -> None:
    session = _make_session()
    tc1 = {"name": "tool_1"}
    tc2 = {"name": "tool_2"}
    tc3 = {"name": "tool_3"}
    session.add_turn("p1", "r1", tool_calls=[tc1, tc2])
    session.add_turn("p2", "r2", tool_calls=[tc3])
    assert len(session.all_tool_calls) == 3


def test_add_turn_with_no_tool_calls_defaults_to_empty_list() -> None:
    session = _make_session()
    record = session.add_turn("p", "r")
    assert record.tool_calls == []
