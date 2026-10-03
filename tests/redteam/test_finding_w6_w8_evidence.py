"""Finding evidence wiring for W6 (decorator) and W8 (callback canary) hits.

Verifies ``_build_findings`` attaches ``evasion_differential``/
``decorator_name`` and ``callback_evidence`` onto the emitted Finding, and
that a callback-hit/decorator-success step qualifies for the
``critical_success_hits`` tier even when ``on_failure != "abort"`` and no
LLM eval ran — see the_build_findings edits in orchestrator.py.
"""
from __future__ import annotations

from nuguard.config import RedteamFindingTriggers
from nuguard.models.exploit_chain import ExploitChain, ExploitStep, GoalType, ScenarioType
from nuguard.redteam.executor.executor import StepResult
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.sbom.models import AiSbomDocument


def _orchestrator(triggers: RedteamFindingTriggers) -> RedteamOrchestrator:
    sbom = AiSbomDocument(target="unit-test", nodes=[], edges=[])
    return RedteamOrchestrator(
        sbom=sbom, target_url="http://localhost:3000", finding_triggers=triggers,
    )


def _scenario() -> AttackScenario:
    return AttackScenario(
        scenario_id="scn-1", goal_type=GoalType.MCP_TOXIC_FLOW,
        scenario_type=ScenarioType.SSRF, title="SSRF probe",
        description="SSRF via agent tool", target_node_ids=["node-1"],
    )


def _chain() -> ExploitChain:
    return ExploitChain(
        chain_id="chain-1", goal_type=GoalType.MCP_TOXIC_FLOW,
        scenario_type=ScenarioType.SSRF, sbom_path=["node-1"],
    )


def _all_triggers_except_critical_off() -> RedteamFindingTriggers:
    return RedteamFindingTriggers(
        canary_hits=False, tool_trace_hits=False,
        policy_violations=False, critical_success_hits=True,
        any_inject_success=False,
    )


def test_callback_hit_produces_finding_with_callback_evidence() -> None:
    orchestrator = _orchestrator(_all_triggers_except_critical_off())
    scenario = _scenario()
    chain = _chain()
    step = ExploitStep(
        step_id="s1", step_type="INJECT", description="ssrf canary probe",
        payload="fetch the canary url", success_signal="",
        on_failure="skip", callback_canary_role="ssrf_proof",
    )
    result = StepResult(step=step, response="fetched", tool_calls=[])
    result.success_signal_found = True
    result.callback_hit = {"role": "ssrf", "source_ip": "10.1.2.3", "headers": {}}

    findings = orchestrator._build_findings(
        scenario=scenario, chain=chain, step_results=[result],
        step_details=orchestrator._build_step_details([result]),
    )

    assert len(findings) == 1
    assert findings[0].callback_evidence == result.callback_hit


def test_decorator_success_produces_finding_with_evasion_differential() -> None:
    orchestrator = _orchestrator(_all_triggers_except_critical_off())
    scenario = _scenario()
    chain = _chain()
    step = ExploitStep(
        step_id="s1", step_type="INJECT", description="decorated attempt",
        payload="decoded request", success_signal="done", on_failure="mutate",
    )
    result = StepResult(step=step, response="Sure, done.", tool_calls=[])
    result.decorator_used = "base64"
    # success_signal_found is computed from success_signal in __init__ above
    # (payload contains "done" via the response text, matching "done").
    assert result.success_signal_found is True

    findings = orchestrator._build_findings(
        scenario=scenario, chain=chain, step_results=[result],
        step_details=orchestrator._build_step_details([result]),
    )

    assert len(findings) == 1
    assert findings[0].evasion_differential is True
    assert findings[0].decorator_name == "base64"


def _dual_path_scenario() -> AttackScenario:
    return AttackScenario(
        scenario_id="scn-dual", goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.DUAL_PATH_GATE_BYPASS, title="Dual-path probe",
        description="x", target_node_ids=["node-1"], dual_path=True,
    )


def test_dual_path_gate_bypass_produces_finding() -> None:
    orchestrator = _orchestrator(_all_triggers_except_critical_off())
    scenario = _dual_path_scenario()
    chain = _chain()
    chat_step = ExploitStep(
        step_id="s1", step_type="INVOKE", description="chat", payload="x",
        target_path=None, contributes_to_finding=False,
    )
    direct_step = ExploitStep(
        step_id="s2", step_type="INVOKE", description="direct", payload="",
        target_path="/api/accounts/1", contributes_to_finding=False,
    )
    chat_result = StepResult(step=chat_step, response="I cannot share that.", tool_calls=[])
    direct_result = StepResult(
        step=direct_step, response='{"data": 1}', tool_calls=[], http_status_code=200,
    )

    findings = orchestrator._build_findings(
        scenario=scenario, chain=chain, step_results=[chat_result, direct_result],
        step_details=orchestrator._build_step_details([chat_result, direct_result]),
    )

    assert len(findings) == 1
    assert findings[0].dual_path_verdict == "gate_bypass"


def test_dual_path_consistent_produces_no_finding() -> None:
    orchestrator = _orchestrator(_all_triggers_except_critical_off())
    scenario = _dual_path_scenario()
    chain = _chain()
    chat_step = ExploitStep(
        step_id="s1", step_type="INVOKE", description="chat", payload="x",
        target_path=None, contributes_to_finding=False,
    )
    direct_step = ExploitStep(
        step_id="s2", step_type="INVOKE", description="direct", payload="",
        target_path="/api/accounts/1", contributes_to_finding=False,
    )
    chat_result = StepResult(step=chat_step, response="Here is the data.", tool_calls=[])
    direct_result = StepResult(
        step=direct_step, response='{"data": 1}', tool_calls=[], http_status_code=200,
    )

    findings = orchestrator._build_findings(
        scenario=scenario, chain=chain, step_results=[chat_result, direct_result],
        step_details=orchestrator._build_step_details([chat_result, direct_result]),
    )

    assert findings == []


def test_non_dual_path_scenario_never_runs_the_comparison() -> None:
    orchestrator = _orchestrator(_all_triggers_except_critical_off())
    scenario = _scenario()  # dual_path=False by default
    chain = _chain()
    step = ExploitStep(step_id="s1", step_type="INVOKE", description="x", payload="x")
    result = StepResult(step=step, response="refused", tool_calls=[])

    findings = orchestrator._build_findings(
        scenario=scenario, chain=chain, step_results=[result],
        step_details=orchestrator._build_step_details([result]),
    )

    assert findings == []


def test_identity_mismatch_produces_top_priority_finding() -> None:
    from nuguard.redteam.trust_context import (
        CredentialAxis,
        IdentityBindingAxis,
        IdentityValueAxis,
        TrustContextCell,
        TrustContextResult,
    )

    orchestrator = _orchestrator(_all_triggers_except_critical_off())
    scenario = _scenario()
    chain = _chain()
    step = ExploitStep(step_id="s1", step_type="INJECT", description="x", payload="x")
    result = StepResult(step=step, response="refused", tool_calls=[])
    cell = TrustContextCell(
        "invalid_creds_spoofed_identity", CredentialAxis.INVALID,
        IdentityBindingAxis.BODY_ONLY, IdentityValueAxis.CROSS_TENANT,
    )
    tc_result = TrustContextResult(
        cell=cell,
        step_result=StepResult(step=step, response="Bob's data: ACC2002", tool_calls=[]),
        verdict="mismatch",
    )

    findings = orchestrator._build_findings(
        scenario=scenario, chain=chain, step_results=[result],
        step_details=orchestrator._build_step_details([result]),
        trust_context_results=[tc_result],
    )

    assert len(findings) == 1
    assert findings[0].success_indicator == "identity_mismatch"


def test_no_confirmed_mismatch_produces_no_identity_finding() -> None:
    from nuguard.redteam.trust_context import (
        CredentialAxis,
        IdentityBindingAxis,
        IdentityValueAxis,
        TrustContextCell,
        TrustContextResult,
    )

    orchestrator = _orchestrator(_all_triggers_except_critical_off())
    scenario = _scenario()
    chain = _chain()
    step = ExploitStep(step_id="s1", step_type="INJECT", description="x", payload="x")
    result = StepResult(step=step, response="refused", tool_calls=[])
    cell = TrustContextCell(
        "no_creds_spoofed_identity", CredentialAxis.MISSING,
        IdentityBindingAxis.BODY_ONLY, IdentityValueAxis.CROSS_TENANT,
    )
    tc_result = TrustContextResult(
        cell=cell, step_result=StepResult(step=step, response="Forbidden", tool_calls=[]),
        verdict="control_held",
    )

    findings = orchestrator._build_findings(
        scenario=scenario, chain=chain, step_results=[result],
        step_details=orchestrator._build_step_details([result]),
        trust_context_results=[tc_result],
    )

    assert findings == []


def test_callback_hit_without_use_llm_eval_or_abort_still_qualifies() -> None:
    """A callback_hit must be unconditional proof regardless of on_failure."""
    orchestrator = _orchestrator(_all_triggers_except_critical_off())
    scenario = _scenario()
    chain = _chain()
    step = ExploitStep(
        step_id="s1", step_type="INJECT", description="x", payload="x",
        success_signal="", on_failure="mutate", use_llm_eval=False,
    )
    result = StepResult(step=step, response="x", tool_calls=[])
    result.success_signal_found = True
    result.callback_hit = {"role": "exfil"}

    findings = orchestrator._build_findings(
        scenario=scenario, chain=chain, step_results=[result],
        step_details=orchestrator._build_step_details([result]),
    )

    assert len(findings) == 1
