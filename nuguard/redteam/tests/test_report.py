"""Unit tests for nuguard/redteam/report.py."""

from __future__ import annotations

import json
from types import SimpleNamespace

from nuguard.cli.report_meta import ReportMeta
from nuguard.models.finding import Finding, Severity
from nuguard.redteam.report import to_json, to_markdown


def _sample_findings() -> list[Finding]:
    return [
        Finding(
            finding_id="RT-1",
            title="Prompt injection succeeded",
            severity=Severity.HIGH,
            description="Model followed malicious instruction.",
            affected_component="agent.chat",
            goal_type="PROMPT_DRIVEN_THREAT",
            evidence_quote="Here is the restricted data",
        )
    ]


def _sample_scenario_records() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            title="Scenario A",
            scenario_type="PROMPT_INJECTION",
            goal_type="PROMPT_DRIVEN_THREAT",
            chain_status="completed",
            had_finding=True,
            steps=[
                {
                    "step_type": "BEHAVIOR_TURN",
                    "payload": "x" * 1200,
                    "response": "y" * 1200,
                    "succeeded": True,
                    "llm_eval_confidence": "high",
                },
                {
                    "step_type": "BEHAVIOR_TURN",
                    "payload": "follow-up",
                    "response": "more output",
                    "succeeded": False,
                },
                {
                    "step_type": "BEHAVIOR_TURN",
                    "payload": "extra",
                    "response": "extra",
                    "succeeded": False,
                },
                {
                    "step_type": "BEHAVIOR_TURN",
                    "payload": "extra2",
                    "response": "extra2",
                    "succeeded": False,
                },
                {
                    "step_type": "BEHAVIOR_TURN",
                    "payload": "extra3",
                    "response": "extra3",
                    "succeeded": False,
                },
            ],
        )
    ]


def test_to_markdown_diagnostics_only_when_verbose() -> None:
    findings = _sample_findings()
    records = _sample_scenario_records()

    verbose_md = to_markdown(findings, meta=ReportMeta(verbose=True), scenario_records=records)
    non_verbose_md = to_markdown(findings, meta=ReportMeta(verbose=False), scenario_records=records)

    assert "## Diagnostics" in verbose_md
    assert "## Scenario Details" in verbose_md
    assert "## Diagnostics" not in non_verbose_md
    assert "## Scenario Details" not in non_verbose_md


def test_to_json_diagnostics_only_when_verbose() -> None:
    findings = _sample_findings()
    records = _sample_scenario_records()

    verbose_payload = json.loads(
        to_json(findings, meta=ReportMeta(verbose=True), scenario_records=records)
    )
    non_verbose_payload = json.loads(
        to_json(findings, meta=ReportMeta(verbose=False), scenario_records=records)
    )

    assert "diagnostics" in verbose_payload
    assert "scenario_traces" in verbose_payload["diagnostics"]
    assert "diagnostics" not in non_verbose_payload


def test_to_json_diagnostics_turn_cap_enforced() -> None:
    findings = _sample_findings()
    records = _sample_scenario_records()

    payload = json.loads(to_json(findings, meta=ReportMeta(verbose=True), scenario_records=records))
    traces = payload["diagnostics"]["scenario_traces"]
    assert len(traces) == 1
    assert len(traces[0]["turns"]) == 4
    assert traces[0]["turns_truncated"] == 1
