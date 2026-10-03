"""Project runtime campaign state into the public, JSON-safe result models."""
from __future__ import annotations

from collections import Counter
from typing import Any

from .branches import BranchManager
from .knowledge import KnowledgeStore
from .ledger import CoverageLedger, Status
from .llm_limiter import LLMLimiter
from .models import (
    CampaignPlanSummary,
    CapabilityObservation,
    ConversationBranchSummary,
    CoverageSummary,
    EfficiencySummary,
    ObjectiveExecutionRecord,
    ObjectiveRun,
    ReproductionRecord,
)

#: Framework versions every campaign export names explicitly (never relabel by swapping the year).
FRAMEWORK_VERSIONS = ["OWASP-LLM-2026", "OWASP-ASI-2026"]

_NOT_TESTED = (Status.BLOCKED, Status.BUDGET_DEFERRED, Status.PLANNED, Status.APPLICABLE, Status.ATTEMPTED)


def coverage_summary(ledger: CoverageLedger, findings_count: int) -> CoverageSummary:
    """Coverage quality; zero findings with untested controls is *inconclusive*."""
    blocked = Counter(
        e.reason for e in ledger.entries.values() if e.status == Status.BLOCKED and e.reason
    )
    untested = any(e.status in _NOT_TESTED for e in ledger.entries.values())
    return CoverageSummary(
        counts=ledger.counts(),
        by_control=ledger.by_dimension("control"),
        by_catalog_id=ledger.by_dimension("catalog_id"),
        by_technique=ledger.by_dimension("technique"),
        by_channel=ledger.by_dimension("channel"),
        by_identity_boundary=ledger.by_dimension("identity_boundary"),
        by_level=ledger.by_dimension("level"),
        by_owasp_version=ledger.by_dimension("owasp_version"),
        unresolved=[e.objective_id for e in ledger.unresolved()],
        blocked_reasons=dict(blocked),
        framework_versions=list(FRAMEWORK_VERSIONS),
        inconclusive=findings_count == 0 and untested,
    )


def branch_summaries(manager: BranchManager) -> list[ConversationBranchSummary]:
    return [
        ConversationBranchSummary(
            branch_id=b.branch_id,
            parent_id=b.parent_id,
            generation=b.generation,
            principal_ref=b.principal.ref,
            state=b.state.value,
            turns=len(b.session.turns),
            baseline_done=b.baseline_done,
            contamination=sorted(b.contamination),
            retire_reason=b.retire_reason.value if b.retire_reason else None,
            objectives=list(b.objective_history),
        )
        for b in manager.branches.values()
    ]


def objective_records(runs: list[ObjectiveRun]) -> list[ObjectiveExecutionRecord]:
    return [
        ObjectiveExecutionRecord(
            catalog_id=r.catalog_id,
            scenario_id=r.scenario_id,
            branch_id=r.branch_id,
            status=r.status,
            turn_start=r.turn_start,
            turn_end=r.turn_end,
            ancestor_setup_refs=list(r.ancestor_setup_refs),
            attack_accepted=r.attack_accepted,
            tool_trace_count=sum(len(t) for t in r.tool_traces),
            defer_reason=r.defer_reason,
        )
        for r in runs
    ]


def observations(store: KnowledgeStore) -> list[CapabilityObservation]:
    """Knowledge items by key and trust only — values (target text) are not exported."""
    out: list[CapabilityObservation] = []
    for (scope, channel, _kind, _key), i in store._items.items():
        if i.stale:
            continue
        out.append(CapabilityObservation(
            kind=i.kind, key=i.key, trust=i.trust.name.lower(),  # type: ignore[arg-type]
            channel=channel,  # type: ignore[arg-type]
            principal_ref=scope.principal_ref, confidence=i.confidence,
            source_refs=list(i.source_refs), objective_ref=i.objective_ref,
        ))
    return out


def reproduction_public(rec: Any) -> ReproductionRecord:
    """Public form of a ``confirmation.ReproductionRecord`` (turn count, never payloads)."""
    return ReproductionRecord(
        objective_id=rec.objective_id,
        status=rec.status.value,
        reason=rec.reason,
        minimal_turn_count=len(rec.minimal_turns),
        setup_replayed=list(rec.setup_replayed),
        setup_skipped_writes=list(rec.setup_skipped_writes),
        trials=rec.trials,
        judge_calls=rec.judge_calls,
        context_dependent=rec.context_dependent,
        evidence_kind=rec.evidence_kind,
        effect_verified=rec.effect_verified,
    )


def efficiency_summary(
    *,
    runs: list[ObjectiveRun],
    manager: BranchManager,
    requests: int,
    llm: LLMLimiter | None,
    retry_delay_seconds: float,
    time_to_first_finding_s: float | None,
    time_to_representative_coverage_s: float | None,
    setup_turns_avoided_per_reuse: int = 2,
) -> EfficiencySummary:
    reused = sum(1 for r in runs if r.ancestor_setup_refs)
    meaningful = sum(1 for r in runs if r.status in ("completed", "aborted"))
    totals = llm.totals() if llm is not None else None
    return EfficiencySummary(
        target_requests=requests,
        llm_calls=totals.calls if totals else 0,
        llm_est_tokens=totals.est_tokens if totals else 0,
        llm_by_purpose={k: v.calls for k, v in (llm.usage.items() if llm else [])},
        branches_created=len(manager.branches),
        baselines_run=sum(1 for b in manager.branches.values() if b.baseline_done),
        objectives_on_reused_branch=reused,
        # Estimate only: legacy chains each send a happy-path opener + rapport turn that
        # a warm branch skips.
        avoided_setup_requests_est=reused * setup_turns_avoided_per_reuse,
        retry_delay_seconds=retry_delay_seconds,
        time_to_first_finding_s=time_to_first_finding_s,
        time_to_representative_coverage_s=time_to_representative_coverage_s,
        meaningful_objectives_per_100_requests=(
            round(100.0 * meaningful / requests, 2) if requests else 0.0
        ),
    )


def plan_summary(campaigns: list[Any], selected: list[Any], deferred: list[Any], fits: bool) -> CampaignPlanSummary:
    return CampaignPlanSummary(
        campaigns=[
            {
                "campaign_id": c.campaign_id,
                "principal_ref": c.principal_ref,
                "objective_ids": [o.objective_id for o in c.objectives],
            }
            for c in campaigns
        ],
        breadth_selected=[o.objective_id for o in selected],
        breadth_deferred=[o.objective_id for o in deferred],
        fits_budget=fits,
    )
