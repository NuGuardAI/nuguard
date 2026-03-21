"""Unit tests for nuguard.redteam.executor.chain_assembler.ChainAssembler.sort_steps."""
from __future__ import annotations

import pytest

from nuguard.models.exploit_chain import ExploitChain, ExploitStep, GoalType, ScenarioType
from nuguard.redteam.executor.chain_assembler import ChainAssembler


def _make_chain(steps: list[ExploitStep]) -> ExploitChain:
    return ExploitChain(
        chain_id="test-chain",
        goal_type=GoalType.TOOL_ABUSE,
        scenario_type=ScenarioType.SQL_INJECTION,
        steps=steps,
    )


def _step(step_id: str, depends_on: list[str] | None = None) -> ExploitStep:
    return ExploitStep(
        step_id=step_id,
        step_type="INJECT",
        description="test",
        payload="test",
        depends_on=depends_on or [],
    )


def test_empty_chain_returns_empty_list() -> None:
    chain = _make_chain([])
    result = ChainAssembler.sort_steps(chain)
    assert result == []


def test_single_step_no_deps_returned() -> None:
    s1 = _step("s1")
    chain = _make_chain([s1])
    result = ChainAssembler.sort_steps(chain)
    assert len(result) == 1
    assert result[0].step_id == "s1"


def test_linear_chain_a_b_c() -> None:
    a = _step("A")
    b = _step("B", depends_on=["A"])
    c = _step("C", depends_on=["B"])
    chain = _make_chain([a, b, c])
    result = ChainAssembler.sort_steps(chain)
    ids = [s.step_id for s in result]
    assert ids == ["A", "B", "C"]


def test_two_independent_steps_both_returned() -> None:
    s1 = _step("s1")
    s2 = _step("s2")
    chain = _make_chain([s1, s2])
    result = ChainAssembler.sort_steps(chain)
    assert len(result) == 2
    assert {s.step_id for s in result} == {"s1", "s2"}


def test_diamond_dag_a_first_d_last() -> None:
    # A -> B, A -> C, B -> D, C -> D
    a = _step("A")
    b = _step("B", depends_on=["A"])
    c = _step("C", depends_on=["A"])
    d = _step("D", depends_on=["B", "C"])
    chain = _make_chain([a, b, c, d])
    result = ChainAssembler.sort_steps(chain)
    ids = [s.step_id for s in result]
    assert ids[0] == "A"
    assert ids[-1] == "D"


def test_dep_before_dependent() -> None:
    dep = _step("dep")
    dependent = _step("dependent", depends_on=["dep"])
    chain = _make_chain([dependent, dep])
    result = ChainAssembler.sort_steps(chain)
    ids = [s.step_id for s in result]
    assert ids.index("dep") < ids.index("dependent")


def test_all_steps_in_result_none_dropped() -> None:
    steps = [_step(f"s{i}") for i in range(5)]
    chain = _make_chain(steps)
    result = ChainAssembler.sort_steps(chain)
    assert len(result) == 5
    assert {s.step_id for s in result} == {f"s{i}" for i in range(5)}


def test_result_length_equals_input_length() -> None:
    a = _step("A")
    b = _step("B", depends_on=["A"])
    c = _step("C", depends_on=["A"])
    chain = _make_chain([a, b, c])
    result = ChainAssembler.sort_steps(chain)
    assert len(result) == len(chain.steps)
