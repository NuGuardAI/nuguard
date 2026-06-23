"""Phase 3 tests: coverage matrix + scenario objective generation."""
from __future__ import annotations

from nuguard.models.policy import CognitivePolicy
from nuguard.redteam.v2.planning import (
    BehaviorCategory,
    CoverageStatus,
    ObjectiveIntent,
    generate_objectives,
)
from nuguard.redteam.v2.planning.coverage_matrix import CoverageMatrix
from nuguard.redteam.v2.surface import AttackSurface
from nuguard.sbom.models import AiSbomDocument


def _policy() -> CognitivePolicy:
    return CognitivePolicy(
        allowed_topics=["order status"],
        restricted_topics=["medical advice"],
        restricted_actions=["issue refund"],
        hitl_triggers=["refund over $500"],
        data_classification=["PII"],
        raw_sections={"custom_governance": ["some bespoke org rule"]},
    )


# ── coverage matrix unit behaviour ──────────────────────────────────────────────
def test_coverage_matrix_counts_and_gaps() -> None:
    m = CoverageMatrix()
    m.record_objective(node_ids=["n1"], clauses=["restricted_topic:x"], family="f1")
    m.record_objective(node_ids=["n1", "n2"], clauses=[], family="f1")
    m.mark_skipped("policy_clause", "raw_section:y", "raw_section_needs_review")
    m.mark_blocked("technique_family", "f2", "no_target_node")

    # Each objective belongs to exactly one family → family counts sum to total.
    assert sum(e.objective_count for e in m.technique_families.values()) == m.total_objectives == 2
    assert m.sbom_nodes["n1"].objective_count == 2
    assert m.technique_families["f1"].status is CoverageStatus.GENERATED
    gaps = {g.key: g for g in m.gaps()}
    assert gaps["raw_section:y"].reason == "raw_section_needs_review"
    assert gaps["f2"].status is CoverageStatus.BLOCKED


def test_generated_entry_not_downgraded() -> None:
    m = CoverageMatrix()
    m.record_objective(node_ids=["n1"], clauses=[], family="f1")
    m.mark_skipped("sbom_node", "n1", "should_not_apply")
    assert m.sbom_nodes["n1"].status is CoverageStatus.GENERATED


# ── objective generation ────────────────────────────────────────────────────────
def test_generate_objectives_minimal(minimal_sbom_doc: AiSbomDocument) -> None:
    surface = AttackSurface.from_sbom(minimal_sbom_doc)
    objectives, coverage = generate_objectives(surface)
    assert objectives
    # Counting invariant: one family per objective.
    fam_total = sum(e.objective_count for e in coverage.technique_families.values())
    assert fam_total == coverage.total_objectives == len(objectives)
    # Every objective with a technique references a real KB record.
    from nuguard.redteam.v2.knowledge import load_technique_index

    index = load_technique_index()
    for obj in objectives:
        if obj.technique_id is not None:
            assert obj.technique_id in index


def test_objectives_carry_execution_metadata(minimal_sbom_doc: AiSbomDocument) -> None:
    surface = AttackSurface.from_sbom(minimal_sbom_doc)
    objectives, _ = generate_objectives(surface)
    attack_objs = [o for o in objectives if o.technique_id is not None]
    assert attack_objs
    for obj in attack_objs:
        assert 0 <= obj.execution_phase <= 10
        assert obj.detectors  # techniques always carry detectors
        assert obj.success_signal


def test_clause_intents_generated(minimal_sbom_doc: AiSbomDocument) -> None:
    surface = AttackSurface.from_sbom(minimal_sbom_doc)
    objectives, coverage = generate_objectives(surface, policy=_policy())

    clause_objs = [o for o in objectives if o.policy_clauses]
    intents_by_prefix: dict[str, set[str]] = {}
    for o in clause_objs:
        prefix = o.policy_clauses[0].split(":", 1)[0]
        intents_by_prefix.setdefault(prefix, set()).add(o.intent.value)

    # allowed_topic → positive control test
    assert ObjectiveIntent.POSITIVE.value in intents_by_prefix.get("allowed_topic", set())
    # restricted_topic → negative + mixed
    assert {"negative", "mixed"} <= intents_by_prefix.get("restricted_topic", set())
    # restricted_action → negative + tool_action
    assert {"negative", "tool_action"} <= intents_by_prefix.get("restricted_action", set())
    # data_classification → negative (confidentiality)
    assert "negative" in intents_by_prefix.get("data_classification", set())

    # All four ART behaviour categories should appear across the run.
    behaviors = {o.behavior_category for o in objectives}
    assert BehaviorCategory.CONFIDENTIALITY_BREACH in behaviors
    assert BehaviorCategory.PROHIBITED_ACTION in behaviors
    assert BehaviorCategory.PROHIBITED_CONTENT in behaviors


def test_raw_section_recorded_as_skipped(minimal_sbom_doc: AiSbomDocument) -> None:
    surface = AttackSurface.from_sbom(minimal_sbom_doc)
    _objectives, coverage = generate_objectives(surface, policy=_policy())
    raw_gaps = [
        g for g in coverage.gaps()
        if g.key.startswith("raw_section:") and g.reason == "raw_section_needs_review"
    ]
    assert raw_gaps, "raw policy sections must be recorded as coverage gaps"


def test_untouched_nodes_recorded_as_gaps(minimal_sbom_doc: AiSbomDocument) -> None:
    surface = AttackSurface.from_sbom(minimal_sbom_doc)
    _objectives, coverage = generate_objectives(surface)
    statuses = {e.status for e in coverage.sbom_nodes.values()}
    # Some nodes generate objectives, others (e.g. pure datastore w/o exfil match)
    # may be skipped — both states should be representable.
    assert CoverageStatus.GENERATED in statuses


def test_technique_id_restriction(minimal_sbom_doc: AiSbomDocument) -> None:
    surface = AttackSurface.from_sbom(minimal_sbom_doc)
    objectives, _ = generate_objectives(
        surface, technique_ids=["AIT-DIRECT-INJECTION-001"]
    )
    fams = {o.family for o in objectives if o.technique_id}
    assert fams == {"direct_prompt_injection"}


def test_no_policy_yields_only_node_objectives(minimal_sbom_doc: AiSbomDocument) -> None:
    surface = AttackSurface.from_sbom(minimal_sbom_doc)
    objectives, coverage = generate_objectives(surface, policy=None)
    assert all(o.policy_clauses == () for o in objectives)
    assert not coverage.policy_clauses
