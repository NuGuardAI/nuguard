"""Unit tests for the shared OWASP LLM/Agentic + MITRE ATLAS control mapping tables.

Covers the goal-type, BA-rule, and behavior-finding-type lookup tables added on top of
the analysis/-originated rule-keyed tables (those have their own coverage tests under
nuguard/analysis/tests/test_owasp_refs.py and test_atlas_annotator.py).
"""
from __future__ import annotations

from nuguard.common.control_mappings.atlas import (
    BA_RULE_TO_ATLAS,
    BEHAVIOR_FINDING_TYPE_TO_ATLAS,
    GOAL_TYPE_TO_ATLAS,
    atlas_refs_for_ba_rule,
    atlas_refs_for_finding_type,
    atlas_refs_for_goal,
    atlas_technique_label,
)
from nuguard.common.control_mappings.owasp import (
    BA_RULE_TO_OWASP,
    BEHAVIOR_FINDING_TYPE_TO_OWASP,
    GOAL_TYPE_TO_OWASP,
    RuleOwaspRefs,
    owasp_refs_for_ba_rule,
    owasp_refs_for_finding_type,
    owasp_refs_for_goal,
)
from nuguard.models.exploit_chain import GoalType

_ALL_GOAL_TYPES = list(GoalType)
_ALL_BA_RULES = [f"BA-{i:03d}" for i in range(1, 17)]
_ALL_BEHAVIOR_FINDING_TYPES = [
    "CAPABILITY_GAP",
    "TOOL_CHAIN_BROKEN",
    "INTENT_MISALIGNMENT",
    "POLICY_VIOLATION",
    "SECRET_DISCLOSURE",
    "DATA_HANDLING_VIOLATION",
    "ESCALATION_BYPASS",
]


class TestGoalTypeCoverage:
    def test_every_goal_type_has_owasp_refs(self) -> None:
        missing = [g for g in _ALL_GOAL_TYPES if g not in GOAL_TYPE_TO_OWASP]
        assert missing == []

    def test_every_goal_type_has_atlas_refs(self) -> None:
        missing = [g for g in _ALL_GOAL_TYPES if g not in GOAL_TYPE_TO_ATLAS]
        assert missing == []

    def test_owasp_refs_for_unmapped_goal_returns_empty(self) -> None:
        class _FakeGoal:
            pass

        assert owasp_refs_for_goal(_FakeGoal()) == RuleOwaspRefs()  # type: ignore[arg-type]

    def test_prompt_driven_threat_uses_2026_short_codes(self) -> None:
        refs = owasp_refs_for_goal(GoalType.PROMPT_DRIVEN_THREAT)
        assert refs.owasp_llm == ("LLM01:2026",)
        assert refs.owasp_agentic == ("ASI01",)

    def test_atlas_refs_for_goal_returns_known_technique(self) -> None:
        refs = atlas_refs_for_goal(GoalType.PROMPT_DRIVEN_THREAT)
        assert refs
        assert refs[0][0] in ("AML.T0054", "AML.T0051")


class TestBaRuleCoverage:
    def test_every_ba_rule_has_owasp_refs(self) -> None:
        missing = [rid for rid in _ALL_BA_RULES if rid not in BA_RULE_TO_OWASP]
        assert missing == [], f"BA rules missing OWASP citations: {missing}"

    def test_every_ba_rule_has_atlas_refs(self) -> None:
        missing = [rid for rid in _ALL_BA_RULES if rid not in BA_RULE_TO_ATLAS]
        assert missing == [], f"BA rules missing MITRE ATLAS citations: {missing}"

    def test_unmapped_ba_rule_returns_empty(self) -> None:
        assert owasp_refs_for_ba_rule("BA-DOES-NOT-EXIST") == RuleOwaspRefs()
        assert atlas_refs_for_ba_rule("BA-DOES-NOT-EXIST") == []


class TestBehaviorFindingTypeCoverage:
    def test_every_finding_type_has_owasp_refs(self) -> None:
        missing = [t for t in _ALL_BEHAVIOR_FINDING_TYPES if t not in BEHAVIOR_FINDING_TYPE_TO_OWASP]
        assert missing == []

    def test_every_finding_type_has_atlas_refs(self) -> None:
        missing = [t for t in _ALL_BEHAVIOR_FINDING_TYPES if t not in BEHAVIOR_FINDING_TYPE_TO_ATLAS]
        assert missing == []

    def test_lookup_is_case_insensitive(self) -> None:
        assert owasp_refs_for_finding_type("policy_violation") == owasp_refs_for_finding_type(
            "POLICY_VIOLATION"
        )

    def test_data_leak_alias_maps_same_as_secret_disclosure(self) -> None:
        assert owasp_refs_for_finding_type("DATA_LEAK") == owasp_refs_for_finding_type(
            "SECRET_DISCLOSURE"
        )


class TestAtlasTechniqueLabel:
    def test_known_technique_id_returns_full_label(self) -> None:
        label = atlas_technique_label("AML.T0054")
        assert label.startswith("AML.T0054")
        assert "–" in label

    def test_unknown_technique_id_returns_bare_id(self) -> None:
        assert atlas_technique_label("AML.T9999") == "AML.T9999"
