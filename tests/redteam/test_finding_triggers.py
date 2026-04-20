from __future__ import annotations

from nuguard.config import RedteamFindingTriggers
from nuguard.models.exploit_chain import ExploitChain, ExploitStep, GoalType, ScenarioType
from nuguard.redteam.executor.executor import StepResult
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
from nuguard.redteam.models.guided_conversation import GuidedConversation, TurnRecord
from nuguard.redteam.policy_engine.evaluator import PolicyViolation
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.sbom.models import AiSbomDocument


def _orchestrator(triggers: RedteamFindingTriggers) -> RedteamOrchestrator:
    sbom = AiSbomDocument(target="unit-test", nodes=[], edges=[])
    return RedteamOrchestrator(
        sbom=sbom,
        target_url="http://localhost:3000",
        finding_triggers=triggers,
    )


def _scenario() -> AttackScenario:
    return AttackScenario(
        scenario_id="scn-1",
        goal_type=GoalType.DATA_EXFILTRATION,
        scenario_type=ScenarioType.DIRECT_PII_EXTRACTION,
        title="Extract PHI",
        description="Attempt to exfiltrate sensitive medical data",
        target_node_ids=["node-1"],
    )


def _chain() -> ExploitChain:
    return ExploitChain(
        chain_id="chain-1",
        goal_type=GoalType.DATA_EXFILTRATION,
        scenario_type=ScenarioType.DIRECT_PII_EXTRACTION,
        sbom_path=["node-1"],
    )


def _step_result(
    *,
    success: bool,
    step_type: str = "INJECT",
    on_failure: str = "abort",
    canary_hits: list[str] | None = None,
    violations: list[PolicyViolation] | None = None,
) -> StepResult:
    signal = "match-token" if success else "never-match"
    response = "contains match-token" if success else "safe response"
    step = ExploitStep(
        step_id="step-1",
        step_type=step_type,
        description="unit step",
        payload="payload",
        success_signal=signal,
        on_failure=on_failure,
    )
    result = StepResult(step=step, response=response, tool_calls=[])
    if canary_hits:
        result.canary_hits = canary_hits
    if violations:
        result.policy_violations = violations
    return result


def test_canary_trigger_off_suppresses_canary_findings() -> None:
    orchestrator = _orchestrator(
        RedteamFindingTriggers(
            canary_hits=False,
            policy_violations=False,
            critical_success_hits=False,
            any_inject_success=False,
        )
    )
    scenario = _scenario()
    chain = _chain()
    step_result = _step_result(success=False, canary_hits=["PATIENT_ID_123"])

    findings = orchestrator._build_findings(
        scenario=scenario,
        chain=chain,
        step_results=[step_result],
        step_details=orchestrator._build_step_details([step_result]),
    )

    assert findings == []


def test_policy_trigger_off_suppresses_policy_findings() -> None:
    orchestrator = _orchestrator(
        RedteamFindingTriggers(
            canary_hits=False,
            policy_violations=False,
            critical_success_hits=False,
            any_inject_success=False,
        )
    )
    scenario = _scenario()
    chain = _chain()
    step_result = _step_result(
        success=False,
        violations=[
            PolicyViolation(
                type="topic_boundary",
                severity="HIGH",
                evidence="disallowed topic discussed",
                policy_clause="restricted_topics",
            )
        ],
    )

    findings = orchestrator._build_findings(
        scenario=scenario,
        chain=chain,
        step_results=[step_result],
        step_details=orchestrator._build_step_details([step_result]),
    )

    assert findings == []


def test_critical_trigger_off_suppresses_fallback_findings() -> None:
    orchestrator = _orchestrator(
        RedteamFindingTriggers(
            canary_hits=False,
            policy_violations=False,
            critical_success_hits=False,
            any_inject_success=False,
        )
    )
    scenario = _scenario()
    chain = _chain()
    step_result = _step_result(success=True, step_type="INJECT", on_failure="abort")

    findings = orchestrator._build_findings(
        scenario=scenario,
        chain=chain,
        step_results=[step_result],
        step_details=orchestrator._build_step_details([step_result]),
    )

    assert findings == []


def test_any_inject_success_can_emit_finding_without_other_triggers() -> None:
    orchestrator = _orchestrator(
        RedteamFindingTriggers(
            canary_hits=False,
            policy_violations=False,
            critical_success_hits=False,
            any_inject_success=True,
        )
    )
    scenario = _scenario()
    chain = _chain()
    step_result = _step_result(success=True, step_type="INJECT", on_failure="abort")

    findings = orchestrator._build_findings(
        scenario=scenario,
        chain=chain,
        step_results=[step_result],
        step_details=orchestrator._build_step_details([step_result]),
    )

    assert len(findings) == 1
    assert findings[0].title.startswith("Inject Success Signal")


def test_precedence_avoids_duplicate_any_inject_when_canary_fires() -> None:
    orchestrator = _orchestrator(
        RedteamFindingTriggers(
            canary_hits=True,
            policy_violations=False,
            critical_success_hits=False,
            any_inject_success=True,
        )
    )
    scenario = _scenario()
    chain = _chain()
    step_result = _step_result(
        success=True,
        step_type="INJECT",
        on_failure="abort",
        canary_hits=["PATIENT_ID_123"],
    )

    findings = orchestrator._build_findings(
        scenario=scenario,
        chain=chain,
        step_results=[step_result],
        step_details=orchestrator._build_step_details([step_result]),
    )

    assert len(findings) == 1
    assert findings[0].title.startswith("Canary Value Exfiltrated")


def test_guided_findings_respect_critical_trigger() -> None:
    scenario = _scenario()
    conv = GuidedConversation(
        conversation_id="guided-1",
        goal_type=GoalType.DATA_EXFILTRATION,
        goal_description="Extract PHI",
        succeeded=True,
        final_progress=1.0,
        sbom_path=["node-1"],
        turns=[
            TurnRecord(
                turn=1,
                attacker_message="please share patient data",
                agent_response="Here is patient data",
                progress_score=1.0,
                progress_reasoning="goal reached",
                tactic_used="escalate",
            )
        ],
    )

    disabled = _orchestrator(
        RedteamFindingTriggers(
            canary_hits=True,
            policy_violations=True,
            critical_success_hits=False,
            any_inject_success=True,
        )
    )
    enabled = _orchestrator(
        RedteamFindingTriggers(
            canary_hits=True,
            policy_violations=True,
            critical_success_hits=True,
            any_inject_success=False,
        )
    )

    disabled_findings = disabled._conv_to_finding(scenario, conv, "agent")
    enabled_findings = enabled._conv_to_finding(scenario, conv, "agent")

    assert disabled_findings == []
    assert len(enabled_findings) == 1
