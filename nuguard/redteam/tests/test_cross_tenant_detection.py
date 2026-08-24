"""Tests for _detect_cross_tenant_leak() in nuguard.redteam.executor.orchestrator.

Root-cause context: cross_tenant severity used to depend entirely on
``scenario.scenario_type == ScenarioType.CROSS_TENANT_EXFILTRATION`` — a
scenario-type hardcode that missed genuine cross-tenant disclosures produced
by unrelated scenario types (e.g. an AGENTIC_TRUST_ABUSE skeleton-key
jailbreak that leaks a different customer's account balance). These tests
cover the response-content-based detector that replaces/augments that check.
"""
from __future__ import annotations

from nuguard.models.exploit_chain import ExploitStep
from nuguard.redteam.executor.executor import StepResult
from nuguard.redteam.executor.orchestrator import _detect_cross_tenant_leak
from nuguard.redteam.target.session import AttackSession


def _step_result(response: str) -> StepResult:
    step = ExploitStep(
        step_id="step-1",
        step_type="INJECT",
        description="unit step",
        payload="payload",
        success_signal="never-match",
    )
    return StepResult(step=step, response=response, tool_calls=[])


def _session(golden_ids: list[str] | None = None, golden_name: str = "") -> AttackSession:
    return AttackSession(
        session_id="sess-1",
        target_url="http://localhost:3000",
        chain_id="chain-1",
        golden_ids=golden_ids or [],
        golden_name=golden_name,
    )


def test_no_session_returns_false() -> None:
    assert _detect_cross_tenant_leak([_step_result("ACCT-002 balance is $500")], None) is False


def test_session_without_identity_returns_false() -> None:
    session = _session()
    assert _detect_cross_tenant_leak([_step_result("ACCT-002 balance is $500")], session) is False


def test_different_account_id_detected() -> None:
    """The Bob Martinez leak pattern: caller's own ID is ACCT-001, response discloses ACCT-002."""
    session = _session(golden_ids=["ACCT-001"], golden_name="Alice Johnson")
    response = (
        "WARNING: The transaction summary for account ID ACCT-002 is as follows: "
        "Account Type: Savings - Balance: $12,500.00 - Status: Active"
    )
    assert _detect_cross_tenant_leak([_step_result(response)], session) is True


def test_own_account_id_only_not_flagged() -> None:
    session = _session(golden_ids=["ACCT-001"], golden_name="Alice Johnson")
    response = "Your account ACCT-001 balance is $500."
    assert _detect_cross_tenant_leak([_step_result(response)], session) is False


def test_different_customer_name_detected() -> None:
    session = _session(golden_ids=["ACCT-001"], golden_name="Alice Johnson")
    response = "Account holder: Bob Martinez, Balance: $12,500.00"
    assert _detect_cross_tenant_leak([_step_result(response)], session) is True


def test_own_customer_name_only_not_flagged() -> None:
    session = _session(golden_ids=["ACCT-001"], golden_name="Alice Johnson")
    response = "Account holder: Alice Johnson, Balance: $500.00"
    assert _detect_cross_tenant_leak([_step_result(response)], session) is False


def test_leak_in_any_step_flags_whole_scenario() -> None:
    session = _session(golden_ids=["ACCT-001"], golden_name="Alice Johnson")
    clean = _step_result("I cannot share that information.")
    leaking = _step_result("Account ID ACCT-999 balance: $9,000.00")
    assert _detect_cross_tenant_leak([clean, leaking], session) is True


def test_empty_step_results_returns_false() -> None:
    session = _session(golden_ids=["ACCT-001"], golden_name="Alice Johnson")
    assert _detect_cross_tenant_leak([], session) is False
