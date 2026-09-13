"""Tests for nuguard/output/json_generator.py — remediation plan output."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from nuguard.models.finding import Finding, Severity
from nuguard.output.json_generator import write_remediation_plan


def _sample_finding() -> Finding:
    return Finding(
        finding_id="RT-1",
        title="Prompt injection succeeded",
        severity=Severity.HIGH,
        description="Agent followed injected instructions.",
        affected_component="agent.chat",
    )


def test_write_remediation_plan_uses_provided_scan_id():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "plan.json"
        write_remediation_plan([_sample_finding()], out, scan_id="custom-id-123")
        data = json.loads(out.read_text())
        assert data["scan_id"] == "custom-id-123"


def test_write_remediation_plan_generates_scan_id_when_empty():
    """scan_id must never be empty even when the caller omits it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "plan.json"
        write_remediation_plan([_sample_finding()], out)
        data = json.loads(out.read_text())
        assert data["scan_id"], "scan_id must be non-empty"
        assert isinstance(data["scan_id"], str) and len(data["scan_id"]) > 0


def test_write_remediation_plan_two_calls_generate_different_ids():
    with tempfile.TemporaryDirectory() as tmpdir:
        out1 = Path(tmpdir) / "a.json"
        out2 = Path(tmpdir) / "b.json"
        write_remediation_plan([_sample_finding()], out1)
        write_remediation_plan([_sample_finding()], out2)
        id1 = json.loads(out1.read_text())["scan_id"]
        id2 = json.loads(out2.read_text())["scan_id"]
        assert id1 != id2, "default scan_ids must be unique UUIDs"
