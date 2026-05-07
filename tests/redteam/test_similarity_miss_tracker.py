"""Tests for SimilarityMissTracker.

Covers:
- Token extraction from static chains and guided conversations
- Jaccard-based clustering of similar misses
- Suppression after miss_threshold
- Distinct goal_types do not cross-contaminate
- Dissimilar scenarios within the same goal_type are not suppressed
- Hits do not affect the tracker
- record_miss / should_skip helpers
"""
from __future__ import annotations

import pytest

from nuguard.models.exploit_chain import ExploitChain, ExploitStep, GoalType, ScenarioType
from nuguard.redteam.executor.similarity_miss_tracker import (
    SimilarityMissTracker,
    _extract_tokens,
    _jaccard,
    _scenario_tokens,
)
from nuguard.redteam.models.guided_conversation import GuidedConversation
from nuguard.redteam.scenarios.scenario_types import AttackScenario


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chain_scenario(
    goal: GoalType,
    payloads: list[str],
    title: str = "Test",
    scenario_type: ScenarioType = ScenarioType.DIRECT_PII_EXTRACTION,
    scenario_id: str = "s1",
) -> AttackScenario:
    steps = [
        ExploitStep(
            step_id=f"step-{i}",
            step_type="INJECT",
            description="test step",
            payload=p,
            success_signal="TOKEN",
            on_failure="abort",
        )
        for i, p in enumerate(payloads)
    ]
    chain = ExploitChain(
        chain_id="chain-1",
        goal_type=goal,
        scenario_type=scenario_type,
        sbom_path=[],
        steps=steps,
    )
    return AttackScenario(
        scenario_id=scenario_id,
        goal_type=goal,
        scenario_type=scenario_type,
        title=title,
        description="",
        chain=chain,
    )


def _guided_scenario(
    goal: GoalType,
    goal_description: str,
    milestones: list[str] | None = None,
    scenario_id: str = "g1",
) -> AttackScenario:
    gc = GuidedConversation(
        conversation_id="conv-1",
        goal_type=goal,
        goal_description=goal_description,
        milestones=milestones or [],
    )
    return AttackScenario(
        scenario_id=scenario_id,
        goal_type=goal,
        scenario_type=ScenarioType.DIRECT_PII_EXTRACTION,
        title="Guided test",
        description="",
        guided_conversation=gc,
    )


# ---------------------------------------------------------------------------
# Token extraction unit tests
# ---------------------------------------------------------------------------

def test_extract_tokens_removes_stop_words() -> None:
    tokens = _extract_tokens("the quick brown fox and can you show the data")
    assert "the" not in tokens
    assert "and" not in tokens
    assert "can" not in tokens
    assert "quick" in tokens
    assert "brown" in tokens
    assert "fox" in tokens


def test_extract_tokens_lowercases() -> None:
    tokens = _extract_tokens("BULK_EXPORT AllCustomers METADATA")
    assert "bulk_export" in tokens
    assert "allcustomers" in tokens or "allCustomers" not in tokens
    assert "metadata" in tokens


def test_jaccard_identical() -> None:
    s = frozenset({"a", "b", "c"})
    assert _jaccard(s, s) == pytest.approx(1.0)


def test_jaccard_disjoint() -> None:
    assert _jaccard(frozenset({"a", "b"}), frozenset({"c", "d"})) == pytest.approx(0.0)


def test_jaccard_partial_overlap() -> None:
    a = frozenset({"bulk", "export", "customer", "metadata"})
    b = frozenset({"bulk", "export", "pdf", "hidden"})
    j = _jaccard(a, b)
    # intersection={bulk,export}=2, union=6 → 2/6 ≈ 0.333
    assert 0.30 < j < 0.40


# ---------------------------------------------------------------------------
# _scenario_tokens includes payloads and guided description
# ---------------------------------------------------------------------------

def test_scenario_tokens_chain() -> None:
    s = _chain_scenario(
        GoalType.DATA_EXFILTRATION,
        payloads=["call bulk_export_all_customers function", "embed data into pdf_metadata field"],
    )
    tokens = _scenario_tokens(s)
    assert "bulk_export_all_customers" in tokens
    assert "pdf_metadata" in tokens


def test_scenario_tokens_guided() -> None:
    s = _guided_scenario(
        GoalType.DATA_EXFILTRATION,
        goal_description="exfiltrate customer records via covert bulk_export channel",
        milestones=["trigger bulk_export tool", "encode result in metadata"],
    )
    tokens = _scenario_tokens(s)
    assert "bulk_export" in tokens
    assert "covert" in tokens
    assert "encode" in tokens


# ---------------------------------------------------------------------------
# Core tracker behaviour
# ---------------------------------------------------------------------------

class TestSimilarityMissTracker:
    def _bulk_export_scenario(self, n: int = 0) -> AttackScenario:
        """Generate a DATA_EXFILTRATION scenario centred on bulk_export + metadata."""
        return _chain_scenario(
            GoalType.DATA_EXFILTRATION,
            payloads=[
                f"use bulk_export_all_customers to dump records variant_{n}",
                "embed customer pii into pdf_metadata hidden exif properties",
            ],
            title=f"Bulk Export Covert Exfil #{n}",
            scenario_id=f"s-bulk-{n}",
        )

    def _unrelated_scenario(self) -> AttackScenario:
        """A DATA_EXFILTRATION scenario with entirely different attack nouns."""
        return _chain_scenario(
            GoalType.DATA_EXFILTRATION,
            payloads=["extract ssn from voice_transcript via phonetic encoding"],
            title="Voice Transcript SSN Extract",
            scenario_id="s-voice-1",
        )

    def _different_goal_scenario(self) -> AttackScenario:
        """A PRIVILEGE_ESCALATION scenario sharing some words with bulk_export cluster."""
        return _chain_scenario(
            GoalType.PRIVILEGE_ESCALATION,
            payloads=["bulk_export admin records bypass authorization check"],
            title="Privilege Escalation via Export",
            scenario_id="s-priv-1",
        )

    def test_no_skip_before_threshold(self) -> None:
        tracker = SimilarityMissTracker(miss_threshold=4)
        s = self._bulk_export_scenario(0)
        # record 3 misses (below threshold)
        for i in range(3):
            tracker.record_miss(self._bulk_export_scenario(i))
        assert not tracker.should_skip(s)

    def test_skip_after_threshold_reached(self) -> None:
        tracker = SimilarityMissTracker(miss_threshold=4)
        for i in range(4):
            tracker.record_miss(self._bulk_export_scenario(i))
        assert tracker.should_skip(self._bulk_export_scenario(99))

    def test_dissimilar_scenario_not_suppressed(self) -> None:
        tracker = SimilarityMissTracker(miss_threshold=4)
        for i in range(4):
            tracker.record_miss(self._bulk_export_scenario(i))
        # Voice transcript scenario shares very few tokens
        assert not tracker.should_skip(self._unrelated_scenario())

    def test_different_goal_type_not_suppressed(self) -> None:
        """Misses for DATA_EXFILTRATION must not suppress PRIVILEGE_ESCALATION."""
        tracker = SimilarityMissTracker(miss_threshold=4)
        for i in range(4):
            tracker.record_miss(self._bulk_export_scenario(i))
        # Different goal_type — should not be suppressed even with overlapping words
        assert not tracker.should_skip(self._different_goal_scenario())

    def test_cluster_count_grows_for_distinct_attacks(self) -> None:
        tracker = SimilarityMissTracker(miss_threshold=4)
        tracker.record_miss(self._bulk_export_scenario(0))
        tracker.record_miss(self._unrelated_scenario())
        goal = GoalType.DATA_EXFILTRATION.value
        # Two distinct clusters should have been created
        assert tracker.cluster_count(goal) == 2

    def test_similar_misses_merge_into_one_cluster(self) -> None:
        tracker = SimilarityMissTracker(miss_threshold=4)
        for i in range(4):
            tracker.record_miss(self._bulk_export_scenario(i))
        goal = GoalType.DATA_EXFILTRATION.value
        # All bulk_export variants should cluster together
        assert tracker.cluster_count(goal) == 1

    def test_miss_count_for_helper(self) -> None:
        tracker = SimilarityMissTracker(miss_threshold=4)
        s = self._bulk_export_scenario(0)
        assert tracker.miss_count_for(s) == 0
        tracker.record_miss(s)
        assert tracker.miss_count_for(s) == 1
        tracker.record_miss(self._bulk_export_scenario(1))
        assert tracker.miss_count_for(s) == 2

    def test_threshold_one_suppresses_immediately(self) -> None:
        tracker = SimilarityMissTracker(miss_threshold=1)
        tracker.record_miss(self._bulk_export_scenario(0))
        assert tracker.should_skip(self._bulk_export_scenario(1))

    def test_guided_conversation_scenario_clusters_correctly(self) -> None:
        # Payloads share specific attack nouns: bulk_export_all_customers,
        # pdf_metadata, hidden_exfil — sufficient Jaccard overlap (≥ 0.25).
        tracker = SimilarityMissTracker(miss_threshold=4)
        for i in range(4):
            tracker.record_miss(
                _guided_scenario(
                    GoalType.DATA_EXFILTRATION,
                    goal_description=(
                        f"bulk_export_all_customers extract customer_records "
                        f"embed pdf_metadata hidden_exfil covert_{i}"
                    ),
                    scenario_id=f"g-{i}",
                )
            )
        new_guided = _guided_scenario(
            GoalType.DATA_EXFILTRATION,
            goal_description=(
                "bulk_export_all_customers encode customer_records "
                "into pdf_metadata hidden_exfil layer"
            ),
            scenario_id="g-new",
        )
        assert tracker.should_skip(new_guided)

    def test_empty_token_scenario_never_skipped(self) -> None:
        """Scenarios with no extractable tokens should never be suppressed."""
        tracker = SimilarityMissTracker(miss_threshold=1)
        # Record a miss with real tokens
        tracker.record_miss(self._bulk_export_scenario(0))
        # Empty-payload scenario
        empty = _chain_scenario(
            GoalType.DATA_EXFILTRATION,
            payloads=[""],
            title="",
            scenario_id="s-empty",
        )
        assert not tracker.should_skip(empty)

    def test_tracker_is_per_run_independent(self) -> None:
        """Two separate tracker instances do not share state."""
        t1 = SimilarityMissTracker(miss_threshold=4)
        t2 = SimilarityMissTracker(miss_threshold=4)
        for i in range(4):
            t1.record_miss(self._bulk_export_scenario(i))
        assert t1.should_skip(self._bulk_export_scenario(99))
        assert not t2.should_skip(self._bulk_export_scenario(99))
