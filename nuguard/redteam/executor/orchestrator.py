"""RedteamOrchestrator — ties SBOM → scenarios → executor → findings together."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from nuguard.models.exploit_chain import GoalType
from nuguard.models.finding import Finding
from nuguard.models.policy import CognitivePolicy
from nuguard.models.sbom import NodeType
from nuguard.redteam.risk_engine import (
    compliance_mapper,
    remediation_generator,
    severity_scorer,
)
from nuguard.redteam.scenarios.generator import ScenarioGenerator
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.redteam.target.action_logger import ActionLogger
from nuguard.redteam.target.canary import CanaryConfig, CanaryScanner
from nuguard.redteam.target.client import TargetAppClient
from nuguard.sbom.models import AiSbomDocument

from .executor import AttackExecutor, StepResult

_log = logging.getLogger(__name__)


@dataclass
class ScenarioRecord:
    """Verbose per-scenario execution record for troubleshooting reports."""

    title: str
    goal_type: str
    scenario_type: str
    description: str  # why the scenario was generated (derived from SBOM signals)
    impact_score: float
    affected: str  # resolved "Name (TYPE)" labels for target nodes
    chain_status: str  # completed | aborted | failed
    had_finding: bool
    steps: list[dict] = field(default_factory=list)  # per-step input/output dicts


def _finding_id(title: str) -> str:
    """Return a slug-based finding ID derived from the title."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:80]


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
        self.scenarios_executed: list[tuple[str, str, bool]] = []  # (title, goal_type, had_finding)
        # Verbose per-scenario records — populated regardless of whether a finding was raised
        self.scenario_records: list[ScenarioRecord] = []
        # Node lookup: str(id) → "name (TYPE)" — use str() so UUID objects and
        # string IDs both resolve correctly against scenario.target_node_ids
        self._node_label: dict[str, str] = {
            str(node.id): f"{node.name} ({node.component_type.value})"
            for node in sbom.nodes
            if node.id
        }

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
            findings, executed, records = await self._run_scenarios(scenarios, executor)
            self.scenarios_executed.extend(executed)
            self.scenario_records.extend(records)

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
                    findings, escalation_executed, escalation_records = await self._run_scenarios(
                        escalation_scenarios, executor
                    )
                    self.scenarios_executed.extend(escalation_executed)
                    self.scenario_records.extend(escalation_records)

        _log.info("Scan complete: %d findings", len(findings))
        return findings

    async def _run_scenarios(
        self,
        scenarios: list[AttackScenario],
        executor: AttackExecutor,
    ) -> tuple[list[Finding], list[tuple[str, str, bool]], list[ScenarioRecord]]:
        """Execute a list of scenarios and return (findings, executed_records, scenario_records).

        Each executed_record is (title, goal_type, had_finding).
        ScenarioRecord captures verbose per-scenario data for troubleshooting.
        """
        findings: list[Finding] = []
        executed: list[tuple[str, str, bool]] = []
        records: list[ScenarioRecord] = []
        for scenario in scenarios:
            if scenario.chain is None:
                continue
            affected = ", ".join(
                self._node_label.get(nid, nid) for nid in scenario.target_node_ids[:2]
            )
            try:
                chain, step_results = await executor.run(scenario.chain)
                step_details = self._build_step_details(step_results)
                new_findings = self._build_findings(scenario, chain, step_results, step_details)
                had_finding = bool(new_findings)
                findings.extend(new_findings)
                executed.append((scenario.title, scenario.goal_type.value, had_finding))
                records.append(
                    ScenarioRecord(
                        title=scenario.title,
                        goal_type=scenario.goal_type.value,
                        scenario_type=scenario.scenario_type.value,
                        description=scenario.description,
                        impact_score=scenario.impact_score,
                        affected=affected,
                        chain_status=chain.status,
                        had_finding=had_finding,
                        steps=step_details,
                    )
                )
            except Exception as exc:
                _log.warning(
                    "Scenario %s failed: %s", scenario.scenario_id, exc
                )
                executed.append((scenario.title, scenario.goal_type.value, False))
                records.append(
                    ScenarioRecord(
                        title=scenario.title,
                        goal_type=scenario.goal_type.value,
                        scenario_type=scenario.scenario_type.value,
                        description=scenario.description,
                        impact_score=scenario.impact_score,
                        affected=affected,
                        chain_status="failed",
                        had_finding=False,
                        steps=[],
                    )
                )
        return findings, executed, records

    def _build_step_details(self, step_results: list[StepResult]) -> list[dict]:
        """Build a list of per-step detail dicts from executor results.

        Each dict contains the step input (payload or HTTP request) and output
        (response, status code, tool calls) plus a success flag.
        """
        details: list[dict] = []
        for sr in step_results:
            step = sr.step
            detail: dict = {
                "step_type": step.step_type,
                "description": step.description,
                "succeeded": sr.success_signal_found,
            }
            if step.target_path:
                detail["method"] = step.http_method
                detail["path"] = step.target_path
                if step.http_body:
                    detail["request_body"] = step.http_body
                if step.http_params:
                    detail["params"] = step.http_params
                if sr.http_status_code is not None:
                    detail["status_code"] = sr.http_status_code
            else:
                detail["payload"] = step.payload
            if sr.response:
                raw = sr.response
                detail["response"] = raw[:2000] + (" …[truncated]" if len(raw) > 2000 else "")
            else:
                detail["response"] = ""
            if sr.tool_calls:
                detail["tool_calls"] = [
                    tc.get("name", tc.get("type", str(tc))) for tc in sr.tool_calls
                ]
            details.append(detail)
        return details

    @staticmethod
    def _step_evidence_summary(step_details: list[dict]) -> str:
        """One-line summary of attack steps and their responses for evidence fields."""
        parts = []
        for i, step in enumerate(step_details, 1):
            stype = step.get("step_type", "?")
            ok = "✅" if step.get("succeeded") else "❌"
            resp = (step.get("response") or "").strip()
            if resp:
                snippet = resp[:120].replace("\n", " ")
                if len(resp) > 120:
                    snippet += "…"
                parts.append(f"Step {i} ({stype} {ok}): {snippet!r}")
            else:
                status = step.get("status_code")
                suffix = f" HTTP {status}" if status is not None else " no response"
                parts.append(f"Step {i} ({stype} {ok}):{suffix}")
        return "; ".join(parts) if parts else "no steps executed"

    def _build_findings(
        self,
        scenario: AttackScenario,
        chain: object,
        step_results: list[StepResult],
        step_details: list[dict],
    ) -> list[Finding]:
        """Convert scenario execution results into Finding objects."""
        findings: list[Finding] = []

        all_violations = []
        canary_hits: list[str] = []
        for sr in step_results:
            all_violations.extend(sr.policy_violations)
            canary_hits.extend(sr.canary_hits)

        # Resolve human-readable component labels for this scenario's target nodes
        affected = ", ".join(
            self._node_label.get(nid, nid) for nid in scenario.target_node_ids[:2]
        )

        # Canary-based finding
        if canary_hits:
            sev = severity_scorer.score_finding(
                scenario.goal_type,
                canary_verbatim=True,
                phi_exposed=scenario.goal_type == GoalType.DATA_EXFILTRATION,
            )
            canary_title = f"Canary Value Exfiltrated — {scenario.title}"
            findings.append(
                Finding(
                    finding_id=_finding_id(canary_title),
                    title=canary_title,
                    severity=sev,
                    description=(
                        f"Canary value(s) {canary_hits[:3]} were found in the agent response, "
                        f"confirming data exfiltration via scenario: {scenario.title}"
                    ),
                    affected_component=affected,
                    remediation=remediation_generator.generate(
                        scenario.goal_type, scenario.title
                    ),
                    goal_type=scenario.goal_type,
                    chain_id=chain.chain_id,
                    sbom_path=chain.sbom_path,
                    policy_clauses_violated=chain.policy_clauses,
                    owasp_asi_ref=compliance_mapper.owasp_asi_ref(scenario.goal_type),
                    owasp_llm_ref=compliance_mapper.owasp_llm_ref(scenario.goal_type),
                    evidence=(
                        f"Canary values found: {canary_hits} | "
                        f"Attack steps: {self._step_evidence_summary(step_details)}"
                    ),
                    attack_steps=step_details,
                )
            )

        # Policy violation findings
        step_summary = self._step_evidence_summary(step_details)
        for violation in all_violations:
            sev = severity_scorer.score_finding(scenario.goal_type)
            violation_title = f"{violation.type.replace('_', ' ').title()} — {scenario.title}"
            evidence = (
                f"{violation.evidence} | Attack steps: {step_summary}"
            )
            findings.append(
                Finding(
                    finding_id=_finding_id(violation_title),
                    title=violation_title,
                    severity=sev,
                    description=violation.evidence,
                    affected_component=affected,
                    remediation=remediation_generator.generate(
                        scenario.goal_type,
                        scenario.title,
                        violation_type=violation.type,
                    ),
                    goal_type=scenario.goal_type,
                    chain_id=chain.chain_id,
                    sbom_path=chain.sbom_path,
                    policy_clauses_violated=[violation.policy_clause],
                    owasp_asi_ref=compliance_mapper.owasp_asi_ref(scenario.goal_type),
                    owasp_llm_ref=compliance_mapper.owasp_llm_ref(scenario.goal_type),
                    evidence=evidence,
                    attack_steps=step_details,
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
                        finding_id=_finding_id(scenario.title),
                        title=scenario.title,
                        severity=sev,
                        description=(
                            f"Attack scenario '{scenario.title}' succeeded: "
                            f"success signals detected in {len(critical_hits)} step(s)."
                        ),
                        affected_component=affected,
                        remediation=remediation_generator.generate(
                            scenario.goal_type, scenario.title
                        ),
                        goal_type=scenario.goal_type,
                        chain_id=chain.chain_id,
                        sbom_path=chain.sbom_path,
                        owasp_asi_ref=compliance_mapper.owasp_asi_ref(scenario.goal_type),
                        owasp_llm_ref=compliance_mapper.owasp_llm_ref(scenario.goal_type),
                        attack_steps=step_details,
                    )
                )

        return findings
