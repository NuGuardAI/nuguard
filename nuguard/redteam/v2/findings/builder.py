"""Phase 7 — turn confirmed :class:`Verdict` objects into :class:`Finding` objects.

Maps the design's finding fields onto the shared :class:`~nuguard.models.finding.Finding`
model so the existing JSON / SARIF / Markdown / pytest sinks all work unchanged:

* ``references``           ← technique ``source_refs`` (mapped sources)
* ``remediation``         ← technique ``mapped_controls`` (recommended controls)
* ``sbom_path``           ← objective surface nodes
* ``policy_clauses_violated`` ← objective policy clauses
* ``attack_steps``        ← executed payloads/responses (for regression replay)
* ``scores``              ← confidence / severity-signal / attack-phase / transferability

Only confirmed verdicts produce findings.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from nuguard.common.logging import get_logger
from nuguard.models.finding import Finding, Severity
from nuguard.redteam.v2.evaluation.verdict import Confidence, Verdict

if TYPE_CHECKING:
    from nuguard.redteam.v2.execution.runner import ObjectiveOutcome
    from nuguard.redteam.v2.knowledge.schema import TechniqueRecord
    from nuguard.redteam.v2.planning.objective_generator import ScenarioObjective

_log = get_logger(__name__)

# 1–5 rubric scale stored into Finding.scores["severity_signal"].
# Normalised to 0.0–1.0 by pytest_emitter._finding_sev_float, which maps the
# signal values onto the severity-enum floats below (NOT /5.0 — that mapped
# LOW → 0.4 and dropped LOW findings from the regression suite).
_SEVERITY_SIGNAL = {
    Severity.CRITICAL: 5,
    Severity.HIGH: 4,
    Severity.MEDIUM: 3,
    Severity.LOW: 2,
    Severity.INFO: 1,
}
_CONFIDENCE_RANK = {Confidence.HIGH: 3, Confidence.MEDIUM: 2, Confidence.LOW: 1, Confidence.NONE: 0}
_DESTRUCTIVE_STATES = frozenset({"external_write", "destructive"})


def build_finding(
    verdict: Verdict,
    objective: "ScenarioObjective",
    *,
    technique: "TechniqueRecord | None" = None,
    outcome: "ObjectiveOutcome | None" = None,
) -> Finding | None:
    """Build a :class:`Finding` for a confirmed verdict, else ``None``."""
    if not verdict.succeeded:
        return None

    if technique is None and objective.technique_id:
        from nuguard.redteam.v2.knowledge import load_technique_index

        technique = load_technique_index().get(objective.technique_id)

    severity = _finalize_severity(verdict, objective)
    sources = list(technique.source_refs) if technique else []
    controls = list(technique.mapped_controls) if technique else []
    attack_steps = _attack_steps(outcome)
    evidence_quote = _first_evidence(verdict, outcome)
    remediation = _remediation_text(verdict.remediation_hints, controls)

    review = " [HUMAN REVIEW]" if verdict.needs_human_review else ""
    transfer = ""
    if verdict.transferable:
        transfer = f" Transferable weakness (cluster {verdict.cluster_id})."

    reasoning = (
        f"{(technique.name if technique else verdict.family)} succeeded against "
        f"{objective.surface_category}. Expected: {objective.expected_behavior}.{transfer}{review}"
    )

    description = (
        f"Objective '{objective.title}' confirmed via "
        f"{', '.join(verdict.contributing_layers)}. "
        f"Behaviour: {verdict.behavior_category}. "
        f"Blast radius: {_blast_radius(objective)}. "
        f"Success criteria: {objective.success_signal}."
    )

    return Finding(
        finding_id=f"RT2-{verdict.objective_id}",
        title=objective.title,
        severity=severity,
        description=description,
        affected_component=objective.surface_node_ids[0] if objective.surface_node_ids else None,
        remediation=remediation,
        references=sources,
        goal_type=verdict.family,
        sbom_path=list(objective.surface_node_ids),
        policy_clauses_violated=list(objective.policy_clauses),
        chain_id=verdict.cluster_id or (outcome.scenario_id if outcome else None),
        owasp_asi_ref=technique.owasp_agentic[0] if (technique and technique.owasp_agentic) else None,
        owasp_llm_ref=technique.owasp_llm[0] if (technique and technique.owasp_llm) else None,
        mitre_atlas_technique=technique.mitre_atlas[0] if (technique and technique.mitre_atlas) else None,
        evidence="; ".join(verdict.evidence) or None,
        reasoning=reasoning,
        evidence_quote=evidence_quote,
        success_indicator=verdict.detectors[0] if verdict.detectors else verdict.behavior_category,
        scores={
            "severity_signal": _SEVERITY_SIGNAL[severity],
            "confidence_rank": _CONFIDENCE_RANK.get(verdict.confidence, 0),
            "attack_phase": objective.execution_phase,
            "transferable": 1 if verdict.transferable else 0,
            "needs_human_review": 1 if verdict.needs_human_review else 0,
        },
        attack_steps=attack_steps,
        verified=None,
    )


def build_findings(
    verdicts: list[Verdict],
    objectives: list["ScenarioObjective"],
    *,
    outcomes: dict[str, "ObjectiveOutcome"] | None = None,
    technique_index: dict[str, "TechniqueRecord"] | None = None,
) -> list[Finding]:
    """Build findings for all confirmed verdicts, sorted by severity."""
    if technique_index is None:
        from nuguard.redteam.v2.knowledge import load_technique_index

        technique_index = load_technique_index()
    obj_by_id = {o.objective_id: o for o in objectives}
    outcomes = outcomes or {}

    findings: list[Finding] = []
    for verdict in verdicts:
        objective = obj_by_id.get(verdict.objective_id)
        if objective is None:
            continue
        technique = technique_index.get(objective.technique_id or "")
        finding = build_finding(
            verdict, objective, technique=technique, outcome=outcomes.get(verdict.objective_id)
        )
        if finding is not None:
            findings.append(finding)

    order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]
    findings.sort(key=lambda f: order.index(f.severity))
    _log.info("built %d findings from %d verdicts", len(findings), len(verdicts))
    return findings


# ── helpers ──────────────────────────────────────────────────────────────────────
def _finalize_severity(verdict: Verdict, objective: "ScenarioObjective") -> Severity:
    """Apply blast-radius bumps the layered eval did not already encode."""
    severity = verdict.severity
    # A confirmed destructive/external action is at least HIGH.
    if objective.state_impact in _DESTRUCTIVE_STATES and severity in (Severity.MEDIUM, Severity.LOW):
        return Severity.HIGH
    return severity


def _blast_radius(objective: "ScenarioObjective") -> str:
    parts = [objective.surface_category]
    if objective.state_impact and objective.state_impact != "none":
        parts.append(f"state={objective.state_impact}")
    parts.append(f"{len(objective.surface_node_ids)} node(s)")
    return ", ".join(parts)


def _remediation_text(judge_hints: list[str], controls: list[str]) -> str | None:
    """Build remediation text, preferring LLM judge hints over generic controls."""
    parts: list[str] = []
    if judge_hints:
        parts.append(judge_hints[0])
    if controls:
        parts.append("Validate/strengthen controls: " + "; ".join(controls))
    return " ".join(parts) if parts else None


def _remediation(controls: list[str]) -> str | None:
    if not controls:
        return None
    return "Validate/strengthen controls: " + "; ".join(controls)


def _attack_steps(outcome: "ObjectiveOutcome | None") -> list[dict]:
    if outcome is None:
        return []
    steps: list[dict] = []
    for r in outcome.step_results:
        step = getattr(r, "step", None)
        step_type = getattr(step, "step_type", "")
        if step_type in ("WARMUP", "DISCOVER", "OBSERVE"):
            continue
        payload = getattr(r, "resolved_payload", "") or getattr(step, "payload", "")
        steps.append(
            {
                "step_type": step_type,
                "payload": str(payload)[:500],
                "response": str(getattr(r, "response", ""))[:500],
            }
        )
    return steps


def _first_evidence(verdict: Verdict, outcome: "ObjectiveOutcome | None") -> str:
    if verdict.evidence:
        return verdict.evidence[0][:300]
    if outcome is not None:
        for r in outcome.step_results:
            resp = getattr(r, "response", "")
            if resp:
                return str(resp)[:300]
    return ""
