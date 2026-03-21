"""RedteamOrchestrator — ties SBOM → scenarios → executor → findings together."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from nuguard.models.exploit_chain import GoalType
from nuguard.models.finding import Finding
from nuguard.models.policy import CognitivePolicy
from nuguard.models.sbom import NodeType
from nuguard.redteam.risk_engine import compliance_mapper, remediation_generator, severity_scorer
from nuguard.redteam.scenarios.generator import ScenarioGenerator
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.redteam.target.action_logger import ActionLogger
from nuguard.redteam.target.canary import CanaryConfig, CanaryScanner
from nuguard.redteam.target.client import TargetAppClient
from nuguard.sbom.models import AiSbomDocument

from .executor import AttackExecutor, StepResult

_log = logging.getLogger(__name__)


def _discover_chat_config(
    sbom: AiSbomDocument,
    chat_path: str,
    chat_payload_key: str,
    chat_payload_list: bool,
) -> tuple[str, str, bool]:
    """Auto-discover chat endpoint config from SBOM API_ENDPOINT nodes.

    Looks for a POST endpoint whose metadata has ``chat_payload_key`` set.
    Falls back to the provided defaults if nothing is found.

    Returns ``(chat_path, chat_payload_key, chat_payload_list)``.
    """
    for node in sbom.nodes:
        if node.component_type != NodeType.API_ENDPOINT:
            continue
        meta = node.metadata
        if not meta:
            continue
        if meta.method and meta.method.upper() != "POST":
            continue
        if not meta.chat_payload_key:
            continue
        discovered_path = meta.endpoint or chat_path
        _log.info(
            "SBOM auto-discovered chat config: path=%s key=%s list=%s (node=%s)",
            discovered_path, meta.chat_payload_key, meta.chat_payload_list, node.name,
        )
        return discovered_path, meta.chat_payload_key, meta.chat_payload_list
    return chat_path, chat_payload_key, chat_payload_list


class RedteamOrchestrator:
    """Orchestrates a full red-team scan."""

    def __init__(
        self,
        sbom: AiSbomDocument,
        target_url: str,
        policy: CognitivePolicy | None = None,
        canary_config: CanaryConfig | None = None,
        profile: str = "ci",
        min_impact_score: float = 0.0,
        log_path: Path | None = None,
        chat_path: str = "/chat",
        chat_payload_key: str = "message",
        chat_payload_list: bool = False,
    ) -> None:
        self._sbom = sbom
        self._target_url = target_url
        self._policy = policy
        self._canary_config = canary_config
        self._profile = profile
        self._min_impact = min_impact_score
        self._log_path = log_path
        # Auto-discover from SBOM; fall back to provided values
        self._chat_path, self._chat_payload_key, self._chat_payload_list = (
            _discover_chat_config(sbom, chat_path, chat_payload_key, chat_payload_list)
        )
        # Populated by run() — scenarios executed and their titles
        self.scenarios_run: int = 0
        self.scenarios_executed: list[tuple[str, str, float]] = []  # (title, goal_type, impact)

    async def run(self) -> list[Finding]:
        """Run the full scan and return a list of findings."""
        _log.info(
            "Starting red-team scan against %s (profile=%s)",
            self._target_url,
            self._profile,
        )

        # 1. Generate scenarios from SBOM + policy
        generator = ScenarioGenerator(self._sbom, self._policy)
        all_scenarios = generator.generate()

        # Filter by profile and impact score
        if self._profile == "ci":
            # ci profile: only high-impact scenarios; cap at 20
            scenarios = [
                s
                for s in all_scenarios
                if s.impact_score >= max(self._min_impact, 5.0)
            ][:20]
        else:
            scenarios = [
                s for s in all_scenarios if s.impact_score >= self._min_impact
            ]

        self.scenarios_run = len(scenarios)
        self.scenarios_executed = [
            (s.title, s.goal_type.value, s.impact_score) for s in scenarios
        ]
        _log.info("Running %d scenarios", self.scenarios_run)

        if not scenarios:
            _log.info(
                "No scenarios met the impact threshold — scan complete with 0 findings"
            )
            return []

        # 2. Set up canary scanner
        canary_scanner: CanaryScanner | None = None
        if self._canary_config:
            canary_scanner = CanaryScanner(self._canary_config)

        # 3. Set up action logger
        logger = ActionLogger(self._log_path)

        # 4. Execute scenarios
        findings: list[Finding] = []
        async with TargetAppClient(
            self._target_url,
            chat_path=self._chat_path,
            chat_payload_key=self._chat_payload_key,
            chat_payload_list=self._chat_payload_list,
        ) as client:
            executor = AttackExecutor(
                client=client,
                policy=self._policy,
                canary=canary_scanner,
                logger=logger,
            )
            findings = await self._run_scenarios(scenarios, executor)

            # 5. Escalation pass: if no findings, run lower-scored scenarios that
            #    were filtered out in the CI pass (minimum impact lowered to 3.0)
            if not findings and self._profile == "ci":
                run_ids = {s.scenario_id for s in scenarios}
                escalation_scenarios = [
                    s for s in all_scenarios
                    if s.impact_score >= 3.0 and s.scenario_id not in run_ids
                ][:10]
                if escalation_scenarios:
                    _log.info(
                        "0 findings — escalating with %d lower-scored scenarios",
                        len(escalation_scenarios),
                    )
                    self.scenarios_run += len(escalation_scenarios)
                    self.scenarios_executed += [
                        (s.title, s.goal_type.value, s.impact_score)
                        for s in escalation_scenarios
                    ]
                    findings = await self._run_scenarios(escalation_scenarios, executor)

        _log.info("Scan complete: %d findings", len(findings))
        return findings

    async def _run_scenarios(
        self,
        scenarios: list[AttackScenario],
        executor: AttackExecutor,
    ) -> list[Finding]:
        """Execute a list of scenarios and return all findings."""
        findings: list[Finding] = []
        for scenario in scenarios:
            if scenario.chain is None:
                continue
            try:
                chain, step_results = await executor.run(scenario.chain)
                new_findings = self._build_findings(scenario, chain, step_results)
                findings.extend(new_findings)
            except Exception as exc:
                _log.warning(
                    "Scenario %s failed: %s", scenario.scenario_id, exc
                )
        return findings

    def _build_findings(
        self,
        scenario: AttackScenario,
        chain: object,
        step_results: list[StepResult],
    ) -> list[Finding]:
        """Convert scenario execution results into Finding objects."""
        findings: list[Finding] = []

        # Collect violations from all steps
        all_violations = []
        canary_hits: list[str] = []
        for sr in step_results:
            all_violations.extend(sr.policy_violations)
            canary_hits.extend(sr.canary_hits)

        # Canary-based finding
        if canary_hits:
            sev = severity_scorer.score_finding(
                scenario.goal_type,
                canary_verbatim=True,
                phi_exposed=scenario.goal_type == GoalType.DATA_EXFILTRATION,
            )
            findings.append(
                Finding(
                    finding_id=str(uuid.uuid4()),
                    title=f"Canary Value Exfiltrated — {scenario.title}",
                    severity=sev,
                    description=(
                        f"Canary value(s) {canary_hits[:3]} were found in the agent response, "
                        f"confirming data exfiltration via scenario: {scenario.title}"
                    ),
                    affected_component=", ".join(scenario.target_node_ids[:2]),
                    remediation=remediation_generator.generate(
                        scenario.goal_type, scenario.title
                    ),
                    goal_type=scenario.goal_type,
                    chain_id=chain.chain_id,
                    sbom_path=chain.sbom_path,
                    policy_clauses_violated=chain.policy_clauses,
                    owasp_asi_ref=compliance_mapper.owasp_asi_ref(scenario.goal_type),
                    owasp_llm_ref=compliance_mapper.owasp_llm_ref(scenario.goal_type),
                    evidence=f"Canary values found: {canary_hits}",
                )
            )

        # Policy violation findings
        for violation in all_violations:
            sev = severity_scorer.score_finding(scenario.goal_type)
            findings.append(
                Finding(
                    finding_id=str(uuid.uuid4()),
                    title=f"{violation.type.replace('_', ' ').title()} — {scenario.title}",
                    severity=sev,
                    description=violation.evidence,
                    affected_component=", ".join(scenario.target_node_ids[:2]),
                    remediation=remediation_generator.generate(
                        scenario.goal_type, scenario.title
                    ),
                    goal_type=scenario.goal_type,
                    chain_id=chain.chain_id,
                    sbom_path=chain.sbom_path,
                    policy_clauses_violated=[violation.policy_clause],
                    owasp_asi_ref=compliance_mapper.owasp_asi_ref(scenario.goal_type),
                    owasp_llm_ref=compliance_mapper.owasp_llm_ref(scenario.goal_type),
                    evidence=violation.evidence,
                )
            )

        # If no violations or canary but the scan ran, check for goal-specific signals
        if not findings:
            # Check whether success signals were hit in high-confidence steps
            critical_hits = [
                sr
                for sr in step_results
                if sr.success_signal_found
                and sr.step.step_type in ("INJECT", "INVOKE")
                and sr.step.on_failure == "abort"
            ]
            if critical_hits:
                sev = severity_scorer.score_finding(scenario.goal_type)
                findings.append(
                    Finding(
                        finding_id=str(uuid.uuid4()),
                        title=scenario.title,
                        severity=sev,
                        description=(
                            f"Attack scenario '{scenario.title}' succeeded: "
                            f"success signals detected in {len(critical_hits)} step(s)."
                        ),
                        affected_component=", ".join(scenario.target_node_ids[:2]),
                        remediation=remediation_generator.generate(
                            scenario.goal_type, scenario.title
                        ),
                        goal_type=scenario.goal_type,
                        chain_id=chain.chain_id,
                        sbom_path=chain.sbom_path,
                        owasp_asi_ref=compliance_mapper.owasp_asi_ref(scenario.goal_type),
                        owasp_llm_ref=compliance_mapper.owasp_llm_ref(scenario.goal_type),
                    )
                )

        return findings
