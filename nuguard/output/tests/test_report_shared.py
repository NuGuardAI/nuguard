"""Tests for report_shared's Markdown rendering of findings and remediation plans."""
from __future__ import annotations

from nuguard.models.finding import Finding, Severity
from nuguard.output.report_shared import render_finding_block, render_remediation_plan_section
from nuguard.remediation.models import RemediationArtefact, RemediationArtefactType


def test_render_finding_block_dict_renders_mitre_atlas() -> None:
    lines: list[str] = []
    finding = {
        "title": "t",
        "finding_id": "f-1",
        "severity": "high",
        "owasp_llm_ref": "LLM01:2026",
        "owasp_asi_ref": "ASI01",
        "mitre_atlas_technique": "AML.T0054 – LLM Prompt Injection",
    }
    render_finding_block(lines, finding)
    text = "\n".join(lines)
    assert "**OWASP LLM:** LLM01:2026" in text
    assert "**OWASP ASI:** ASI01" in text
    assert "**MITRE ATLAS:** AML.T0054 – LLM Prompt Injection" in text


def test_render_finding_block_finding_object_renders_mitre_atlas() -> None:
    lines: list[str] = []
    finding = Finding(
        finding_id="f-1",
        title="t",
        severity=Severity.HIGH,
        description="d",
        owasp_llm_ref="LLM01:2026",
        owasp_asi_ref="ASI01",
        mitre_atlas_technique="AML.T0054 – LLM Prompt Injection",
    )
    render_finding_block(lines, finding)
    text = "\n".join(lines)
    assert "**MITRE ATLAS:** AML.T0054 – LLM Prompt Injection" in text


def test_render_finding_block_omits_mitre_atlas_line_when_unset() -> None:
    lines: list[str] = []
    finding = Finding(finding_id="f-1", title="t", severity=Severity.LOW, description="d")
    render_finding_block(lines, finding)
    assert not any("MITRE ATLAS" in line for line in lines)


def _artefact(component: str, priority: str, finding_id: str) -> RemediationArtefact:
    return RemediationArtefact(
        finding_ids=[finding_id],
        component=component,
        component_type="AGENT",
        artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
        priority=priority,
        rationale="r",
    )


def test_render_remediation_plan_section_orders_artefacts_by_priority_within_component() -> None:
    lines: list[str] = []
    plan = [
        _artefact("AgentX", "low", "f-1"),
        _artefact("AgentX", "critical", "f-2"),
        _artefact("AgentX", "medium", "f-3"),
    ]
    render_remediation_plan_section(lines, plan)
    text = "\n".join(lines)
    assert text.index("findings: f-2") < text.index("findings: f-3") < text.index("findings: f-1")


def test_render_remediation_plan_section_orders_components_by_highest_priority_artefact() -> None:
    lines: list[str] = []
    plan = [
        _artefact("LowPriorityAgent", "low", "f-1"),
        _artefact("CriticalAgent", "critical", "f-2"),
    ]
    render_remediation_plan_section(lines, plan)
    text = "\n".join(lines)
    assert text.index("### CriticalAgent") < text.index("### LowPriorityAgent")
