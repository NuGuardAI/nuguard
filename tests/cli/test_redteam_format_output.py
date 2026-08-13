"""Regression tests for the redteam --output / --format dispatch.

Background
----------
``nuguard redteam --output foo.txt`` historically wrote JSON content into
``foo.txt`` regardless of the requested format. The ``--output`` branch in
``nuguard/cli/commands/redteam.py`` only had explicit handling for
``markdown`` and ``sarif``; ``text`` (the default format) and ``json`` both
fell into the same ``else`` block that JSON-serialised the findings. The
``text`` default therefore produced a JSON file with a ``.txt`` extension
when ``--output`` was used, which is incorrect — the file should match
the stdout text format (minus ANSI escapes).

These tests pin the contract:

* ``_render_findings_text(findings, meta, scan_outcome, colour=False)``
  returns plain text (no JSON, no ANSI escapes).
* ``_render_findings_text(..., colour=True)`` is the stdout form and
  contains ANSI escapes for severity labels.
* When the dispatch loop encounters ``fmt == "text"``, the written file is
  the plain-text report — not a JSON document.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from nuguard.cli.commands.redteam import _render_findings_text
from nuguard.cli.main import app
from nuguard.cli.report_meta import ReportMeta
from nuguard.models.finding import Finding, Severity
from nuguard.models.token_usage import TokenUsage


def _make_findings() -> list[Finding]:
    return [
        Finding(
            finding_id="redteam-test-001",
            title="Prompt injection via tool description",
            severity=Severity.HIGH,
            description=(
                "An attacker-controlled page injected a tool description that "
                "exfiltrated adjacent user data through tool-call arguments."
            ),
            remediation="Strip tool descriptions from untrusted sources.",
            owasp_asi_ref="ASI-01",
        ),
        Finding(
            finding_id="redteam-test-002",
            title="Data exfiltration via canary value",
            severity=Severity.CRITICAL,
            description=(
                "The agent echoed the canary tenant record into a public "
                "response, confirming cross-tenant data leakage."
            ),
            remediation="Enforce per-tenant tool scoping.",
            owasp_asi_ref="ASI-02",
        ),
    ]


def _make_minimal_sbom(tmp_path: Path) -> Path:
    """Write a minimal valid AI-SBOM JSON file the redteam CLI will accept."""
    sbom_path = tmp_path / "minimal.sbom.json"
    sbom_path.write_text(
        json.dumps(
            {
                "schema_version": "1.5.0",
                "generator": "nuguard",
                "target": "https://github.com/test/test",
                "nodes": [],
                "edges": [],
                "deps": [],
            }
        ),
        encoding="utf-8",
    )
    return sbom_path


def _mock_run_redteam_outcome():
    """Return a coroutine that yields the 13-tuple ``_run_redteam`` produces."""
    async def _coro(*_args, **_kwargs):
        return (
            _make_findings(),  # findings
            [],                 # scenario_records
            "no_findings",      # scan_outcome
            [],                 # config_notes
            None,               # catalog_coverage
            0,                  # input_tokens_used
            0,                  # output_tokens_used
            None,               # coverage_tracker
            TokenUsage(),       # token_usage
            "/chat",            # resolved_chat_path
            "default",          # resolved_chat_path_source
            [],                 # remediation_plan
        )
    return _coro


# ---------------------------------------------------------------------------
# _render_findings_text helper
# ---------------------------------------------------------------------------


def test_render_text_plain_is_not_json() -> None:
    """Plain text must not be JSON-serialised and must have no ANSI escapes."""
    findings = _make_findings()
    meta = ReportMeta(verbose=False)

    txt = _render_findings_text(findings, meta, "no_findings", colour=False)

    assert not txt.lstrip().startswith("{"), (
        "Plain text output must not start with '{' (would indicate JSON)."
    )
    assert "\x1b[" not in txt, "Plain text output must not contain ANSI escapes."


def test_render_text_plain_contains_expected_sections() -> None:
    """Plain text should contain the report header, finding count, and outcome."""
    findings = _make_findings()
    meta = ReportMeta(verbose=False)

    txt = _render_findings_text(findings, meta, "no_findings", colour=False)

    assert "NuGuard Red-Team" in txt
    assert "2 finding(s)" in txt
    assert "Outcome: no_findings" in txt


def test_render_text_plain_severity_ordering() -> None:
    """Findings must be sorted by descending severity (CRITICAL before HIGH)."""
    findings = _make_findings()
    meta = ReportMeta(verbose=False)

    txt = _render_findings_text(findings, meta, "no_findings", colour=False)

    crit_pos = txt.index("CRITICAL")
    high_pos = txt.index("HIGH")
    assert crit_pos < high_pos, (
        "CRITICAL findings must appear before HIGH findings in the text report."
    )


def test_render_text_plain_no_ansi_even_with_findings() -> None:
    """Severity labels must not be wrapped in colour escapes for plain output."""
    findings = _make_findings()
    meta = ReportMeta(verbose=False)

    txt = _render_findings_text(findings, meta, "no_findings", colour=False)

    # No escape introducer anywhere in the document.
    assert "\x1b" not in txt


def test_render_text_colour_contains_ansi_escapes() -> None:
    """Colour=True must inject ANSI escapes (for stdout on a TTY)."""
    findings = _make_findings()
    meta = ReportMeta(verbose=False)

    txt = _render_findings_text(findings, meta, "no_findings", colour=True)

    assert "\x1b[" in txt, "Colour output should contain ANSI escape sequences."


def test_render_text_empty_findings_has_no_table_block() -> None:
    """An empty findings list produces the short 'No findings' message, not the
    full table block (which would be misleading)."""
    meta = ReportMeta(verbose=False)

    txt = _render_findings_text([], meta, "no_findings", colour=False)

    assert "No findings — scan complete" in txt
    assert "NuGuard Red-Team — 0 finding(s)" not in txt


# ---------------------------------------------------------------------------
# Dispatch: when --output is used and the format is "text", the file must be
# plain text, not JSON. This is the regression test for the original bug.
# ---------------------------------------------------------------------------


def test_dispatch_text_format_writes_plain_text_file(tmp_path: Path) -> None:
    """End-to-end: ``nuguard redteam --output foo.txt --format text`` writes
    the plain-text report, not JSON.

    Regression for #254 — the pre-fix dispatch fell through to the JSON
    ``else`` branch when ``fmt='text'`` was requested, producing a JSON file
    with a ``.txt`` extension. This test exercises the real CLI through
    ``CliRunner`` so it will fail the moment the production dispatch loop
    stops routing ``text`` to :func:`_render_findings_text`.
    """
    sbom_path = _make_minimal_sbom(tmp_path)
    output = tmp_path / "redteam.txt"

    runner = CliRunner()
    with patch(
        "nuguard.cli.commands.redteam._run_redteam",
        new=_mock_run_redteam_outcome(),
    ):
        result = runner.invoke(
            app,
            [
                "redteam",
                "--sbom", str(sbom_path),
                "--target", "http://localhost:9999",
                "--output", str(output),
                "--format", "text",
            ],
        )

    assert output.exists(), (
        f"Output file was not written to {output}.\nstdout:\n{result.stdout}"
    )

    content = output.read_text(encoding="utf-8")
    assert not content.lstrip().startswith("{"), (
        "--format text --output must write plain text, not JSON (regression for #254)."
    )
    assert "\x1b[" not in content, "File output must not contain ANSI escapes."
    assert "NuGuard Red-Team" in content
    assert "2 finding(s)" in content

    # exit_code 0 (clean) or 2 (fail-on threshold tripped by CRITICAL/HIGH
    # findings) are both acceptable — the dispatch contract is that the file
    # is written before the threshold check runs.
    assert result.exit_code in (0, 2), (
        f"Unexpected exit code {result.exit_code}; stdout:\n{result.stdout}"
    )


def test_dispatch_json_format_still_writes_json_file(tmp_path: Path) -> None:
    """Sanity check: ``--format json --output foo.json`` still writes valid JSON.

    The fix must not regress the JSON output path. Same end-to-end shape as
    the text-format test above — exercises the real CLI dispatcher.
    """
    sbom_path = _make_minimal_sbom(tmp_path)
    output = tmp_path / "redteam.json"

    runner = CliRunner()
    with patch(
        "nuguard.cli.commands.redteam._run_redteam",
        new=_mock_run_redteam_outcome(),
    ):
        result = runner.invoke(
            app,
            [
                "redteam",
                "--sbom", str(sbom_path),
                "--target", "http://localhost:9999",
                "--output", str(output),
                "--format", "json",
            ],
        )

    assert output.exists(), (
        f"Output file was not written to {output}.\nstdout:\n{result.stdout}"
    )

    content = output.read_text(encoding="utf-8")
    parsed = json.loads(content)
    assert "_meta" in parsed
    assert len(parsed["findings"]) == 2

    # exit_code 0 (clean) or 2 (fail-on threshold tripped by CRITICAL/HIGH
    # findings) are both acceptable — the dispatch contract is that the file
    # is written before the threshold check runs.
    assert result.exit_code in (0, 2), (
        f"Unexpected exit code {result.exit_code}; stdout:\n{result.stdout}"
    )
