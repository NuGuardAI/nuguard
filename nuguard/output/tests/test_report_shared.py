"""Tests for render_finding_block's Markdown rendering of framework citations."""
from __future__ import annotations

from nuguard.models.finding import Finding, Severity
from nuguard.output.report_shared import render_finding_block


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
