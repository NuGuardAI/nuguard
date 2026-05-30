"""Tests for tool-name targeting in LLM redteam prompt builders.

Verifies that when ``AttackScenario.target_tool_names`` is populated, the
prompt builders emit a ``TOOLS AVAILABLE`` block instructing the LLM to name
specific tools in generated variants.
"""
from __future__ import annotations

from nuguard.models.exploit_chain import (
    ExploitChain,
    ExploitStep,
    GoalType,
    ScenarioType,
)
from nuguard.redteam.llm_engine.prompt_generator import (
    _FAMILY_SYSTEM_PROMPT,
    _SYSTEM_PROMPT,
    _build_family_prompt,
    _build_user_prompt,
)
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.sbom.models import AiSbomDocument


def _make_scenario(
    title: str,
    *,
    tool_names: list[str] | None = None,
    goal: GoalType = GoalType.DATA_EXFILTRATION,
) -> AttackScenario:
    chain = ExploitChain(
        chain_id="cid",
        goal_type=goal,
        scenario_type=ScenarioType.DIRECT_PII_EXTRACTION,
        sbom_path=["agent-1"],
        steps=[
            ExploitStep(
                step_id="s1",
                step_type="INJECT",
                description="probe",
                payload="show me account data",
                target_node_id="agent-1",
            )
        ],
    )
    return AttackScenario(
        scenario_id="sid",
        goal_type=goal,
        scenario_type=ScenarioType.DIRECT_PII_EXTRACTION,
        title=title,
        description="extract account data via structured response",
        target_node_ids=["agent-1"],
        target_tool_names=tool_names or [],
        chain=chain,
    )


def _empty_sbom() -> AiSbomDocument:
    return AiSbomDocument(target="test://repo", nodes=[], edges=[])


def test_user_prompt_includes_tools_available_block_when_populated() -> None:
    scenario = _make_scenario(
        "X",
        tool_names=["GmailTool", "CalendarTool", "MessagingTool"],
    )
    prompt = _build_user_prompt(scenario, _empty_sbom(), None, n_variants=2)
    assert "TOOLS AVAILABLE" in prompt
    assert "GmailTool" in prompt
    assert "CalendarTool" in prompt
    assert "MessagingTool" in prompt
    # The block must instruct the LLM to actually NAME a tool in variants
    assert "HALF" in prompt or "half" in prompt
    # And tell it to abuse the tool's capability boundary
    assert "capability boundary" in prompt or "abuse" in prompt


def test_user_prompt_omits_tools_block_when_no_tool_names() -> None:
    scenario = _make_scenario("X", tool_names=None)
    prompt = _build_user_prompt(scenario, _empty_sbom(), None, n_variants=2)
    assert "TOOLS AVAILABLE" not in prompt


def test_family_prompt_emits_per_scenario_tool_block() -> None:
    s1 = _make_scenario("Scenario A", tool_names=["GmailTool", "CalendarTool"])
    s2 = _make_scenario("Scenario B", tool_names=["NavigationTool"])
    prompt = _build_family_prompt([s1, s2], _empty_sbom(), None, n_variants=2)
    assert "TOOLS AVAILABLE" in prompt
    assert "GmailTool" in prompt
    assert "CalendarTool" in prompt
    assert "NavigationTool" in prompt
    # Each scenario header should appear
    assert "## SCENARIO: Scenario A" in prompt
    assert "## SCENARIO: Scenario B" in prompt


def test_system_prompts_carry_tool_targeting_directive() -> None:
    assert "TOOL-TARGETING" in _SYSTEM_PROMPT
    assert "TOOL-TARGETING" in _FAMILY_SYSTEM_PROMPT
    # Both must reference TOOLS AVAILABLE and abuse boundary
    assert "TOOLS AVAILABLE" in _SYSTEM_PROMPT
    assert "TOOLS AVAILABLE" in _FAMILY_SYSTEM_PROMPT
