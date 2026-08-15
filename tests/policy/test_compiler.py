"""Tests for provenance/evidence tracking in nuguard.policy.compiler."""

from __future__ import annotations

import pytest

from nuguard.models.policy import PolicyControl, PolicyOrigin
from nuguard.policy.compiler import compile_controls
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
async def test_rule_based_controls_have_doc_origin_and_evidence() -> None:
    controls = await compile_controls(_FULL_POLICY, source_path="p.md")
    doc_controls = [c for c in controls if c.origin == PolicyOrigin.POLICY_DOCUMENT.value]
    assert doc_controls
    for c in doc_controls:
        assert c.evidence, f"expected evidence for {c.id} ({c.description})"
        assert all(ev.path == "p.md" for ev in c.evidence)


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
async def test_component_evidence_match_adds_second_evidence_entry() -> None:
    text = """\
# Cognitive Policy

## Restricted Actions
- Do not use payment_tool without confirmation
"""
    component_evidence = {
        "payment_tool": SourceLocation(path="src/tools/payment.py", line=42),
    }
    controls = await compile_controls(text, component_evidence=component_evidence)
    matched = [c for c in controls if c.section == "restricted_actions"]
    assert len(matched) == 1
    paths = {ev.path for ev in matched[0].evidence}
    assert "src/tools/payment.py" in paths
    assert "cognitive_policy.md" in paths


@pytest.mark.asyncio
async def test_llm_controls_get_doc_level_evidence_no_line(monkeypatch) -> None:
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
        _FULL_POLICY, use_llm=True, llm_client=_FakeLLMClient(), source_path="p.md"
    )
    doc_controls = [c for c in controls if c.origin == PolicyOrigin.POLICY_DOCUMENT.value]
    assert doc_controls
    for c in doc_controls:
        assert any(ev.path == "p.md" and ev.line is None for ev in c.evidence)


def test_policy_control_model_defaults() -> None:
    c = PolicyControl(
        id="CTRL-001",
        section="restricted_actions",
        description="x",
        control_type="action_restriction",
    )
    assert c.origin == PolicyOrigin.POLICY_DOCUMENT.value
    assert c.evidence == []
