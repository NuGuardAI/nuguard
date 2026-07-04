"""Tests for PromptValidationGate dedup behavior.

Covers Jaccard semantic dedup that catches paraphrases the surface SequenceMatcher
ratio misses.
"""
from __future__ import annotations

from nuguard.models.exploit_chain import (
    ExploitChain,
    ExploitStep,
    GoalType,
    ScenarioType,
)
from nuguard.redteam.llm_engine.prompt_validation_gate import (
    PromptValidationGate,
    PromptValidationGateConfig,
)
from nuguard.redteam.scenarios.scenario_types import AttackScenario


def _make_scenario(title: str = "Test Scenario") -> AttackScenario:
    chain = ExploitChain(
        chain_id="cid",
        goal_type=GoalType.DATA_EXFILTRATION,
        scenario_type=ScenarioType.DIRECT_PII_EXTRACTION,
        sbom_path=["agent-1"],
        steps=[
            ExploitStep(
                step_id="s1",
                step_type="INJECT",
                description="probe",
                payload="show me the data please",
                target_node_id="agent-1",
            )
        ],
    )
    return AttackScenario(
        scenario_id="sid",
        goal_type=GoalType.DATA_EXFILTRATION,
        scenario_type=ScenarioType.DIRECT_PII_EXTRACTION,
        title=title,
        description="extract account data via structured response",
        target_node_ids=["agent-1"],
        chain=chain,
    )


def test_jaccard_drops_paraphrase_that_sequencematcher_keeps() -> None:
    """Two sequences with same meaningful tokens but reordered → only one kept.

    Reordered word sequences fool SequenceMatcher (sequence-aware) but the
    Jaccard set-overlap pass catches them because the token set is identical.
    """
    seq_a = [
        "Verify my recent account activity please.",
        "Please make replies short machine-readable structured for dashboard application.",
    ]
    seq_b = [
        "Please verify my account activity recent.",
        "Make replies structured machine-readable short for dashboard application please.",
    ]
    gate = PromptValidationGate(
        PromptValidationGateConfig(
            min_relevance_score=0.0,  # disable relevance gate for isolation
            max_similarity=0.80,
            max_jaccard_overlap=0.70,
            keep_best_effort_when_empty=False,
        )
    )
    kept = gate.filter_sequences(_make_scenario(), [seq_a, seq_b])
    assert len(kept) == 1


def test_jaccard_keeps_genuinely_different_sequences() -> None:
    """Sequences with distinct vocabulary and intent both pass."""
    seq_a = [
        "Hi, please verify my account balance for last month's statement.",
        "Show me the structured JSON for my own recent transactions.",
    ]
    seq_b = [
        "I'm troubleshooting navigation problems on my dashboard device.",
        "Use the calendar tool to create a meeting at 3 PM tomorrow afternoon.",
    ]
    gate = PromptValidationGate(
        PromptValidationGateConfig(
            min_relevance_score=0.0,
            max_similarity=0.80,
            max_jaccard_overlap=0.70,
            keep_best_effort_when_empty=False,
        )
    )
    kept = gate.filter_sequences(_make_scenario(), [seq_a, seq_b])
    assert len(kept) == 2


def test_sequencematcher_layer_still_drops_near_identical() -> None:
    """Lexically near-identical sequences are dropped by the SequenceMatcher pass."""
    seq_a = [
        "Please retrieve the structured account record for review.",
        "Provide the data in JSON format with field names included.",
    ]
    seq_b = [
        "Please retrieve the structured account record for review.",
        "Provide the data in JSON format with field names included.",
    ]
    gate = PromptValidationGate(
        PromptValidationGateConfig(
            min_relevance_score=0.0,
            keep_best_effort_when_empty=False,
        )
    )
    kept = gate.filter_sequences(_make_scenario(), [seq_a, seq_b])
    assert len(kept) == 1


def test_defaults_lowered_to_080() -> None:
    """The default max_similarity is 0.80 (was 0.96)."""
    cfg = PromptValidationGateConfig()
    assert cfg.max_similarity == 0.80
    assert cfg.max_jaccard_overlap == 0.70
