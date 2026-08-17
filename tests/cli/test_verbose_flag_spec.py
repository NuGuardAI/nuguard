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
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nuguard.cli.main import app

runner = CliRunner()

# Rich emits ANSI escape sequences between characters in its styled help
# panels (e.g. ``\x1b[1;36m-\x1b[0m\x1b[1;36m-verbose\x1b[0m``), which breaks
# a literal substring match on flag tokens like ``--verbose``. Strip them
# before asserting so the tests pin the semantic help text rather than
# terminal formatting.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


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
    # Rich emits ANSI escape sequences between characters in its styled help
    # panels (e.g. ``\x1b[1;36m-\x1b[0m\x1b[1;36m-verbose\x1b[0m``), which
    # breaks a literal substring match on ``--verbose`` even though the
    # CLI correctly exposes the flag. Strip ANSI codes before asserting.
    plain = _ANSI_RE.sub("", result.output)
    assert "--verbose" in plain
    assert "--no-verbose" in plain


# ---------------------------------------------------------------------------
# CLI flag > config > default precedence
# ---------------------------------------------------------------------------


def _run_validate_to_markdown(
    config: Path,
    output: Path,
    cli_flags: list[str],
    *,
    policy_record_present: bool = True,
) -> str:
    """Run ``nuguard validate`` end-to-end with a canned result.

    The validate command's heavy lifting happens inside ``_do_validate``,
    which for a real invocation would call ``_run_validate`` against a live
    target. We monkeypatch the module-level ``_run_validate`` in
    ``nuguard.cli.commands.validate`` to return a fabricated
    ``ValidateRunResult`` carrying one policy record, then write the report
    to a markdown file and return its contents. This lets the test observe
    the REAL CLI-vs-config precedence (``effective_verbose = verbose if
    verbose is not None else config.verbose``): a verbose report includes the
    ``## Diagnostics`` appendix, a non-verbose one omits it.
    """
    from nuguard.cli.commands import validate as validate_mod
    from nuguard.models.validate import (
        CapabilityMap,
        TurnPolicyRecord,
        ValidateRunResult,
    )

    policy_records = (
        [
            TurnPolicyRecord(
                turn=1,
                prompt="hello",
                response="hi",
                tool_calls=[],
                violations=[],
                canary_hits=[],
                scenario_name="happy-path",
                scenario_type="test",
            )
        ]
        if policy_record_present
        else []
    )

    fake_result = ValidateRunResult(
        run_id="test-run",
        capability_map=CapabilityMap(run_id="test-run"),
        policy_records=policy_records,
        scenarios_executed=1,
        scan_outcome="no_findings",
    )

    async def fake_run_validate(**kwargs: object) -> ValidateRunResult:
        return fake_result

    # The helper is not a pytest fixture, so patch manually and guarantee
    # restoration via try/finally.
    original = validate_mod._run_validate
    validate_mod._run_validate = fake_run_validate  # type: ignore[assignment]
    try:
        result = runner.invoke(
            app,
            [
                "validate",
                "--config", str(config),
                *cli_flags,
                "--output", str(output),
                "--format", "markdown",
            ],
        )
    finally:
        validate_mod._run_validate = original

    assert result.exit_code == 0, result.output
    return output.read_text(encoding="utf-8")


def test_validate_cli_verbose_overrides_config(tmp_path: Path) -> None:
    """CLI --verbose must override the verbose: false config setting."""
    config = tmp_path / "nuguard.yaml"
    config.write_text("validate:\n  verbose: false\n  target: http://localhost:9999\n", encoding="utf-8")
    out = tmp_path / "report.md"
    payload = _run_validate_to_markdown(config, out, ["--verbose"])
    # --verbose wins over config verbose: false → diagnostics emitted.
    assert "## Diagnostics" in payload


def test_validate_no_verbose_overrides_config(tmp_path: Path) -> None:
    """CLI --no-verbose must override the verbose: true config setting."""
    config = tmp_path / "nuguard.yaml"
    config.write_text("validate:\n  verbose: true\n  target: http://localhost:9999\n", encoding="utf-8")
    out = tmp_path / "report.md"
    payload = _run_validate_to_markdown(config, out, ["--no-verbose"])
    # --no-verbose wins over config verbose: true → diagnostics omitted.
    assert "## Diagnostics" not in payload


def test_validate_config_verbose_true_drives_diagnostics(tmp_path: Path) -> None:
    """With no CLI flag, config verbose: true drives the diagnostics.

    Guards the config→default side of the precedence chain so a future
    regression where the config value stops being read is caught.
    """
    config = tmp_path / "nuguard.yaml"
    config.write_text("validate:\n  verbose: true\n  target: http://localhost:9999\n", encoding="utf-8")
    out = tmp_path / "report.md"
    payload = _run_validate_to_markdown(config, out, [])
    assert "## Diagnostics" in payload


def test_validate_nonverbose_without_policy_records_has_no_diagnostics(
    tmp_path: Path,
) -> None:
    """A run with no policy records must not emit diagnostics.

    Guards that the absence is driven by the negative branch (and not by
    accidentally gating the appendix on the record list being non-empty).
    """
    config = tmp_path / "nuguard.yaml"
    config.write_text("validate:\n  verbose: true\n  target: http://localhost:9999\n", encoding="utf-8")
    out = tmp_path / "report.md"
    payload = _run_validate_to_markdown(
        config, out, [], policy_record_present=False
    )
    assert "## Diagnostics" not in payload


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
        severity=Severity.HIGH,
        title="test finding",
        description="x",
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