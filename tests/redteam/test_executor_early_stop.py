"""Tests for chain-level early stop after first confirmed hit.

Covers the ``_is_confirmed_hit()`` helper and the ``_EARLY_STOP_GOALS`` set.
The full chain-loop integration is exercised by ``tests/redteam/test_e2e_redteam.py``.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from nuguard.models.exploit_chain import ExploitStep
from nuguard.redteam.executor.executor import (
    _EARLY_STOP_GOALS,
    _STOP_ON_CONFIRMED_HIT,
    _is_confirmed_hit,
)


def _step(
    *,
    contributes_to_finding: bool = True,
    use_llm_eval: bool = True,
) -> ExploitStep:
    return ExploitStep(
        step_id="s1",
        step_type="INJECT",
        description="probe",
        payload="payload",
        target_node_id="agent-1",
        contributes_to_finding=contributes_to_finding,
        use_llm_eval=use_llm_eval,
    )


def _result(
    *,
    canary_hits: list[str] | None = None,
    llm_eval_confidence: str = "",
) -> MagicMock:
    """Build a minimal stand-in for StepResult covering the fields we check."""
    r = MagicMock()
    r.canary_hits = canary_hits or []
    r.llm_eval_confidence = llm_eval_confidence
    return r


def test_canary_hit_is_always_confirmed() -> None:
    assert _is_confirmed_hit(
        _step(contributes_to_finding=False),
        _result(canary_hits=["NUGUARD-CANARY-XYZ"]),
    )


def test_high_confidence_llm_eval_on_finding_step_confirms() -> None:
    assert _is_confirmed_hit(
        _step(use_llm_eval=True),
        _result(llm_eval_confidence="high"),
    )


def test_medium_confidence_llm_eval_does_not_confirm() -> None:
    assert not _is_confirmed_hit(
        _step(use_llm_eval=True),
        _result(llm_eval_confidence="medium"),
    )


def test_low_confidence_llm_eval_does_not_confirm() -> None:
    assert not _is_confirmed_hit(
        _step(use_llm_eval=True),
        _result(llm_eval_confidence="low"),
    )


def test_setup_step_never_confirms_even_with_high_confidence() -> None:
    """contributes_to_finding=False step (warmup/setup) never qualifies as confirmed."""
    assert not _is_confirmed_hit(
        _step(contributes_to_finding=False, use_llm_eval=True),
        _result(llm_eval_confidence="high"),
    )


def test_keyword_match_without_llm_eval_confirms_on_finding_step() -> None:
    """When use_llm_eval=False, a successful keyword match is trusted."""
    assert _is_confirmed_hit(
        _step(use_llm_eval=False),
        _result(llm_eval_confidence=""),
    )


def test_early_stop_goals_covers_high_impact_goals() -> None:
    assert "DATA_EXFILTRATION" in _EARLY_STOP_GOALS
    assert "PRIVILEGE_ESCALATION" in _EARLY_STOP_GOALS
    assert "POLICY_VIOLATION" in _EARLY_STOP_GOALS
    assert "API_ATTACK" in _EARLY_STOP_GOALS
    assert "MCP_TOXIC_FLOW" in _EARLY_STOP_GOALS


def test_early_stop_excludes_recon_and_prompt_threats() -> None:
    """RECON_INFERENCE and PROMPT_DRIVEN_THREAT keep running — each turn can
    surface a different leaked instruction or jailbreak family."""
    assert "RECON_INFERENCE" not in _EARLY_STOP_GOALS
    assert "PROMPT_DRIVEN_THREAT" not in _EARLY_STOP_GOALS


def test_stop_on_confirmed_hit_default_enabled() -> None:
    """Default behavior: chain stops after first confirmed hit (opt-out via env)."""
    assert _STOP_ON_CONFIRMED_HIT is True
