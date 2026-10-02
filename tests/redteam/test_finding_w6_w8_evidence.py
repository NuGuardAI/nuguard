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
