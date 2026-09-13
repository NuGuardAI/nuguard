"""Tests for nuguard/cli/commands/validate.py output renderers."""
from __future__ import annotations

import uuid

from nuguard.cli.commands.validate import (
    _validate_result_to_markdown,
    _validate_result_to_text,
)
from nuguard.cli.report_meta import ReportMeta
from nuguard.models.validate import CapabilityMap, ValidateRunResult


def _sample_result() -> ValidateRunResult:
    run_id = str(uuid.uuid4())
    return ValidateRunResult(
        run_id=run_id,
        findings=[],
        capability_map=CapabilityMap(run_id=run_id),
        scenarios_executed=3,
        scan_outcome="no_findings",
    )


# ── Markdown output ──────────────────────────────────────────────────────────


def test_validate_markdown_hides_run_id_by_default():
    result = _sample_result()
    md = _validate_result_to_markdown(result, meta=ReportMeta())
    assert result.run_id not in md


def test_validate_markdown_shows_run_id_when_verbose():
    result = _sample_result()
    md = _validate_result_to_markdown(result, meta=ReportMeta(verbose=True, run_id=result.run_id))
    assert result.run_id in md


# ── Text output ──────────────────────────────────────────────────────────────


def test_validate_text_hides_run_id_by_default():
    result = _sample_result()
    text = _validate_result_to_text(result, meta=ReportMeta())
    assert result.run_id not in text


def test_validate_text_shows_run_id_when_verbose():
    result = _sample_result()
    text = _validate_result_to_text(result, meta=ReportMeta(verbose=True, run_id=result.run_id))
    assert result.run_id in text
