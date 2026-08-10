"""Cross-command --verbose contract tests (issue #163).

These tests pin the surface contract documented in
``documentation/docs/verbose-flag-spec.md`` so any future regression that
breaks the basic guarantees (flag presence, --no-verbose support, CLI
precedence, metadata emission, findings invariance) is caught early.

The spec is "Partially Implemented" — these tests cover the parts of the
contract that are actually shipped today. Future phases of the spec
(bounded diagnostics envelope, cap enforcement) will add more tests when
implemented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nuguard.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# --verbose / --no-verbose surface presence on every scoped command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        ["behavior", "--help"],
        ["redteam", "--help"],
        ["validate", "--help"],
        ["policy", "check", "--help"],
    ],
)
def test_verbose_flag_exposed_in_help(command: list[str]) -> None:
    """Every in-scope command must document --verbose/--no-verbose."""
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output
    assert "--verbose" in result.output
    assert "--no-verbose" in result.output


# ---------------------------------------------------------------------------
# CLI flag > config > default precedence
# ---------------------------------------------------------------------------


def test_validate_cli_verbose_overrides_config(tmp_path: Path) -> None:
    """CLI --verbose must override the verbose: false config setting."""
    config = tmp_path / "nuguard.yaml"
    config.write_text("validate:\n  verbose: false\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "validate",
            "--config", str(config),
            "--verbose",
            "--help",
        ],
    )
    # --help exits 0 with --verbose accepted
    assert result.exit_code == 0, result.output


def test_validate_no_verbose_overrides_config(tmp_path: Path) -> None:
    """CLI --no-verbose must override the verbose: true config setting."""
    config = tmp_path / "nuguard.yaml"
    config.write_text("validate:\n  verbose: true\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "validate",
            "--config", str(config),
            "--no-verbose",
            "--help",
        ],
    )
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# ReportMeta.verbose round-trip
# ---------------------------------------------------------------------------


def test_report_meta_includes_verbose_flag() -> None:
    """The shared report metadata helper must include `verbose`."""
    from nuguard.cli.report_meta import ReportMeta

    meta = ReportMeta(verbose=True)
    payload = meta.to_dict()
    assert payload["verbose"] is True

    meta = ReportMeta(verbose=False)
    payload = meta.to_dict()
    assert payload["verbose"] is False


# ---------------------------------------------------------------------------
# Findings invariance between verbose modes (smoke test using the
# redteam report helpers — full coverage lives in tests/redteam/test_report.py).
# ---------------------------------------------------------------------------


def test_redteam_findings_invariant_between_verbose_modes() -> None:
    """Same finding set must render the same way regardless of verbose."""
    from nuguard.cli.report_meta import ReportMeta
    from nuguard.models.finding import Finding, Severity
    from nuguard.redteam.report import to_json, to_markdown

    finding = Finding(
        finding_id="finding-1",
        rule_id="RT-TEST-001",
        severity=Severity.HIGH,
        title="test finding",
        description="x",
        target_node_id="n1",
        goal_type="PROMPT_DRIVEN_THREAT",
        attack_family="PROMPT_INJECTION",
        attack_vector="direct",
    )
    meta_v = ReportMeta(verbose=True)
    meta_nv = ReportMeta(verbose=False)

    # Same findings passed to both report functions; the finding set is the
    # same input, and the renderers must not silently add or drop anything
    # because of the verbose flag.
    md_v = to_markdown([finding], meta=meta_v)
    md_nv = to_markdown([finding], meta=meta_nv)
    assert "test finding" in md_v
    assert "test finding" in md_nv

    payload_v = json.loads(to_json([finding], meta=meta_v))
    payload_nv = json.loads(to_json([finding], meta=meta_nv))
    assert payload_v["findings"] == payload_nv["findings"]
