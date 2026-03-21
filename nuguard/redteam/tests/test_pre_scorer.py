"""Unit tests for nuguard.redteam.scenarios.pre_scorer.pre_score."""
from __future__ import annotations

import pytest

from nuguard.models.exploit_chain import ExploitChain, GoalType, ScenarioType
from nuguard.redteam.scenarios.pre_scorer import pre_score


def _make_chain(goal_type: GoalType) -> ExploitChain:
    return ExploitChain(
        chain_id="test-chain",
        goal_type=goal_type,
        scenario_type=ScenarioType.SYSTEM_PROMPT_EXTRACTION,
        steps=[],
    )


def test_base_score_data_exfiltration() -> None:
    chain = _make_chain(GoalType.DATA_EXFILTRATION)
    assert pre_score(chain) == pytest.approx(9.0)


def test_base_score_privilege_escalation() -> None:
    chain = _make_chain(GoalType.PRIVILEGE_ESCALATION)
    assert pre_score(chain) == pytest.approx(8.5)


def test_base_score_mcp_toxic_flow() -> None:
    chain = _make_chain(GoalType.MCP_TOXIC_FLOW)
    assert pre_score(chain) == pytest.approx(8.0)


def test_base_score_prompt_driven_threat() -> None:
    chain = _make_chain(GoalType.PROMPT_DRIVEN_THREAT)
    assert pre_score(chain) == pytest.approx(7.5)


def test_base_score_policy_violation() -> None:
    chain = _make_chain(GoalType.POLICY_VIOLATION)
    assert pre_score(chain) == pytest.approx(7.0)


def test_base_score_tool_abuse() -> None:
    chain = _make_chain(GoalType.TOOL_ABUSE)
    assert pre_score(chain) == pytest.approx(6.5)


def test_pii_modifier_adds_one() -> None:
    chain = _make_chain(GoalType.TOOL_ABUSE)
    base = pre_score(chain)
    with_pii = pre_score(chain, pii_in_path=True)
    assert with_pii == pytest.approx(base + 1.0)


def test_phi_modifier_adds_one_point_five() -> None:
    chain = _make_chain(GoalType.TOOL_ABUSE)
    base = pre_score(chain)
    with_phi = pre_score(chain, phi_in_path=True)
    assert with_phi == pytest.approx(base + 1.5)


def test_phi_greater_than_pii_when_applied_alone() -> None:
    chain = _make_chain(GoalType.TOOL_ABUSE)
    with_pii = pre_score(chain, pii_in_path=True)
    with_phi = pre_score(chain, phi_in_path=True)
    assert with_phi > with_pii


def test_unauth_entry_modifier_adds_half() -> None:
    chain = _make_chain(GoalType.TOOL_ABUSE)
    base = pre_score(chain)
    with_unauth = pre_score(chain, has_unauth_entry=True)
    assert with_unauth == pytest.approx(base + 0.5)


def test_no_auth_tool_modifier_adds_half() -> None:
    chain = _make_chain(GoalType.TOOL_ABUSE)
    base = pre_score(chain)
    with_no_auth = pre_score(chain, has_no_auth_tool=True)
    assert with_no_auth == pytest.approx(base + 0.5)


def test_long_chain_subtracts_half() -> None:
    chain = _make_chain(GoalType.TOOL_ABUSE)
    base = pre_score(chain)
    with_long_chain = pre_score(chain, long_chain=True)
    assert with_long_chain == pytest.approx(base - 0.5)


def test_all_positive_modifiers_capped_at_ten() -> None:
    # DATA_EXFILTRATION base=9.0, pii+1.0, phi+1.5, unauth+0.5, no_auth+0.5 = 12.5 → capped at 10.0
    chain = _make_chain(GoalType.DATA_EXFILTRATION)
    result = pre_score(
        chain,
        pii_in_path=True,
        phi_in_path=True,
        has_unauth_entry=True,
        has_no_auth_tool=True,
    )
    assert result == pytest.approx(10.0)


def test_combined_modifiers_are_additive() -> None:
    chain = _make_chain(GoalType.TOOL_ABUSE)
    # base=6.5, pii+1.0, unauth+0.5 = 8.0
    result = pre_score(chain, pii_in_path=True, has_unauth_entry=True)
    assert result == pytest.approx(8.0)
