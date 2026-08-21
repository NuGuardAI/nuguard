"""Cross-artifact run_id/scan_id correlation tests (#327).

Machine-readable artifacts (redteam JSON ``_meta``, remediation-plan JSON
``scan_id``) must always carry a stable, non-empty identifier so outputs
from one invocation can be correlated reliably.
"""

from __future__ import annotations

import json
from pathlib import Path

from nuguard.cli.report_meta import ReportMeta
from nuguard.models.finding import Finding
from nuguard.output.json_generator import write_remediation_plan
from nuguard.redteam.report import to_json


def test_redteam_json_embeds_meta_run_id() -> None:
    meta = ReportMeta()
    payload = json.loads(to_json([], meta=meta))
    assert payload["_meta"]["run_id"] == meta.run_id


def test_remediation_plan_uses_provided_scan_id(tmp_path: Path) -> None:
    out = tmp_path / "plan.json"
    write_remediation_plan([], out, target_url="http://t", scan_id="run-123")
    assert json.loads(out.read_text(encoding="utf-8"))["scan_id"] == "run-123"


def test_remediation_plan_scan_id_never_empty(tmp_path: Path) -> None:
    """Omitting scan_id must still yield a non-empty identifier."""
    out = tmp_path / "plan.json"
    write_remediation_plan([], out)
    assert json.loads(out.read_text(encoding="utf-8"))["scan_id"]


def test_redteam_and_plan_share_one_invocation_id(
    tmp_path: Path,
) -> None:
    """The redteam JSON run_id flows into the sibling remediation-plan scan_id."""
    meta = ReportMeta()
    payload = json.loads(to_json([_finding()], meta=meta))
    assert payload["_meta"]["run_id"] == meta.run_id

    out = tmp_path / "out.remediation-plan.json"
    write_remediation_plan([_finding()], out, scan_id=meta.run_id)
    assert json.loads(out.read_text(encoding="utf-8"))["scan_id"] == payload["_meta"]["run_id"]


def _finding() -> Finding:
    return Finding(
        finding_id="F-1",
        title="t",
        severity="high",
        description="d",
        evidence="e",
    )
