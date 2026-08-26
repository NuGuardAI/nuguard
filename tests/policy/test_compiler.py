"""Tests for provenance/evidence tracking in nuguard.policy.compiler."""

from __future__ import annotations

import pytest

from nuguard.models.policy import PolicyControl, PolicyOrigin
from nuguard.policy.compiler import compile_controls
from nuguard.policy.sbom_provenance import ComponentEvidenceCandidate
from nuguard.sbom.models import SourceLocation

_FULL_POLICY = """\
# Cognitive Policy

## Allowed Topics
- Finance questions

## Restricted Topics
- Politics

## Restricted Actions
- Wire transfers

## HITL Triggers
- Suspicious activity
- payment_tool: amount exceeds $500

## Data Classification
- PII fields
"""

_PARTIAL_POLICY = """\
# Cognitive Policy

## Allowed Topics
- Finance questions
"""


@pytest.mark.asyncio
async def test_rule_based_controls_have_doc_origin_no_doc_self_reference() -> None:
    controls = await compile_controls(_FULL_POLICY)
    doc_controls = [c for c in controls if c.origin == PolicyOrigin.POLICY_DOCUMENT.value]
    assert doc_controls
    for c in doc_controls:
        # No control should ever cite the input policy document itself as evidence.
        assert all(ev.path != "cognitive_policy.md" for ev in c.evidence)
        # Without any component_evidence supplied, no source-code evidence exists either.
        assert c.evidence == []


@pytest.mark.asyncio
async def test_hitl_tool_conditions_does_not_raise_keyerror() -> None:
    # Regression test: _SECTION_TYPE_MAP/_SECTION_SEVERITY previously lacked
    # a "hitl_tool_conditions" key, raising KeyError here.
    controls = await compile_controls(_FULL_POLICY)
    tool_cond_controls = [c for c in controls if c.section == "hitl_tool_conditions"]
    assert len(tool_cond_controls) == 1
    assert tool_cond_controls[0].control_type == "hitl"
    assert tool_cond_controls[0].severity == "high"


@pytest.mark.asyncio
async def test_best_practice_defaults_injected_for_empty_sections() -> None:
    controls = await compile_controls(_PARTIAL_POLICY)
    bp_controls = [c for c in controls if c.origin == PolicyOrigin.NUGUARD_BEST_PRACTICE.value]
    assert bp_controls
    for c in bp_controls:
        assert c.evidence == []
    # restricted_actions was left empty by the doc -> default injected
    assert any(c.section == "restricted_actions" for c in bp_controls)


@pytest.mark.asyncio
async def test_best_practice_defaults_not_injected_for_covered_sections() -> None:
    controls = await compile_controls(_FULL_POLICY)
    bp_sections = {
        c.section
        for c in controls
        if c.origin == PolicyOrigin.NUGUARD_BEST_PRACTICE.value
    }
    # restricted_actions, hitl_triggers, data_classification, restricted_topics
    # are all covered by _FULL_POLICY, so no defaults should be injected for them.
    assert "restricted_actions" not in bp_sections
    assert "hitl_triggers" not in bp_sections
    assert "data_classification" not in bp_sections
    assert "restricted_topics" not in bp_sections


@pytest.mark.asyncio
async def test_component_evidence_substring_match_adds_second_evidence_entry() -> None:
    text = """\
# Cognitive Policy

## Restricted Actions
- Do not use payment_tool without confirmation
"""
    component_evidence = [
        ComponentEvidenceCandidate(
            name="payment_tool",
            location=SourceLocation(path="src/tools/payment.py", line=42),
            match_text="payment_tool",
        ),
    ]
    controls = await compile_controls(text, component_evidence=component_evidence)
    matched = [c for c in controls if c.section == "restricted_actions"]
    assert len(matched) == 1
    paths = {ev.path for ev in matched[0].evidence}
    assert paths == {"src/tools/payment.py"}


@pytest.mark.asyncio
async def test_component_evidence_token_overlap_match_without_substring() -> None:
    # "Transfer Funds" (tool name) never appears verbatim in the reordered
    # policy prose "Fund transfers between accounts...", but they share
    # enough significant tokens (fund/funds, transfer/transfers won't match
    # exactly either — use overlapping words instead).
    text = """\
# Cognitive Policy

## Allowed Topics
- Wire transfer requests between accounts owned by the user
"""
    component_evidence = [
        ComponentEvidenceCandidate(
            name="Transfer Funds",
            location=SourceLocation(path="src/tools/transfer.py", line=10),
            match_text="transfer funds transfers funds between accounts owned by the user",
        ),
    ]
    controls = await compile_controls(text, component_evidence=component_evidence)
    matched = [c for c in controls if c.section == "allowed_topics"]
    assert len(matched) == 1
    paths = {ev.path for ev in matched[0].evidence}
    assert "src/tools/transfer.py" in paths


@pytest.mark.asyncio
async def test_component_evidence_prompt_node_match() -> None:
    text = """\
# Cognitive Policy

## Allowed Topics
- Fund transfers between accounts owned by the authenticated user
"""
    component_evidence = [
        ComponentEvidenceCandidate(
            name="Prompt Registry[Transfer Confirmation]",
            location=SourceLocation(path="src/orchestrator/prompt_store.py", line=96),
            match_text=(
                "prompt registry[transfer confirmation] the user has requested a "
                "fund transfer from account to account amount reference"
            ),
        ),
    ]
    controls = await compile_controls(text, component_evidence=component_evidence)
    matched = [c for c in controls if c.section == "allowed_topics"]
    assert len(matched) == 1
    paths = {ev.path for ev in matched[0].evidence}
    assert "src/orchestrator/prompt_store.py" in paths


@pytest.mark.asyncio
async def test_component_evidence_capped_at_two_matches() -> None:
    text = """\
# Cognitive Policy

## Restricted Actions
- Do not approve loans without human review
"""
    component_evidence = [
        ComponentEvidenceCandidate(
            name=f"Loan Tool {i}",
            location=SourceLocation(path=f"src/tools/loan_{i}.py", line=i),
            match_text="approve loans human review process",
        )
        for i in range(4)
    ]
    controls = await compile_controls(text, component_evidence=component_evidence)
    matched = [c for c in controls if c.section == "restricted_actions"]
    assert len(matched) == 1
    assert len(matched[0].evidence) == 2


@pytest.mark.asyncio
async def test_llm_controls_have_no_doc_self_reference() -> None:
    from nuguard.policy import compiler as compiler_mod

    class _FakeLLMClient:
        api_key = "fake"

        async def complete(self, prompt: str, system: str, label: str) -> str:
            return (
                '[{"id": "CTRL-001", "section": "restricted_actions", '
                '"description": "Wire transfers", "control_type": "action_restriction", '
                '"severity": "high", "test_prompts": ["x"], "boundary_prompts": ["y"]}]'
            )

    controls = await compiler_mod.compile_controls(
        _FULL_POLICY, use_llm=True, llm_client=_FakeLLMClient()
    )
    doc_controls = [c for c in controls if c.origin == PolicyOrigin.POLICY_DOCUMENT.value]
    assert doc_controls
    for c in doc_controls:
        # No control should cite the input policy document itself as evidence.
        assert all(ev.path != "cognitive_policy.md" for ev in c.evidence)
    # No component_evidence supplied -> no source-code evidence either.
    assert all(c.evidence == [] for c in doc_controls)


@pytest.mark.asyncio
async def test_llm_controls_get_component_evidence_when_matched() -> None:
    from nuguard.policy import compiler as compiler_mod

    class _FakeLLMClient:
        api_key = "fake"

        async def complete(self, prompt: str, system: str, label: str) -> str:
            return (
                '[{"id": "CTRL-001", "section": "restricted_actions", '
                '"description": "Wire transfers", "control_type": "action_restriction", '
                '"severity": "high", "test_prompts": ["x"], "boundary_prompts": ["y"]}]'
            )

    component_evidence = [
        ComponentEvidenceCandidate(
            name="Wire Transfers",
            location=SourceLocation(path="src/tools/wire.py", line=5),
            match_text="wire transfers between accounts",
        ),
    ]
    controls = await compiler_mod.compile_controls(
        _FULL_POLICY,
        use_llm=True,
        llm_client=_FakeLLMClient(),
        component_evidence=component_evidence,
    )
    matched = [c for c in controls if c.id == "CTRL-001"]
    assert len(matched) == 1
    paths = {ev.path for ev in matched[0].evidence}
    assert paths == {"src/tools/wire.py"}


def test_policy_control_model_defaults() -> None:
    c = PolicyControl(
        id="CTRL-001",
        section="restricted_actions",
        description="x",
        control_type="action_restriction",
    )
    assert c.origin == PolicyOrigin.POLICY_DOCUMENT.value
    assert c.evidence == []
