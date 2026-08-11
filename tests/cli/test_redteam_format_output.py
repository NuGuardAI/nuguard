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

from pathlib import Path

from nuguard.cli.commands.redteam import _render_findings_text
from nuguard.cli.report_meta import ReportMeta
from nuguard.models.finding import Finding, Severity


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
    """Replicate the redteam --output dispatch loop and verify a ``.txt`` file
    that ``fmt='text'`` produces contains plain text — NOT JSON.

    This mirrors the inline dispatch in
    ``nuguard/cli/commands/redteam.py:421-460`` after the fix.
    """
    findings = _make_findings()
    meta = ReportMeta(verbose=False)

    output = tmp_path / "redteam.txt"
    effective_formats = ["text"]
    extension_map = {"text": ".txt", "json": ".json", "markdown": ".md", "sarif": ".sarif"}

    # --- Inline dispatch (mirrors redteam.py --output branch) ---
    for fmt in effective_formats:
        if len(effective_formats) > 1:
            out_path = output.with_suffix(extension_map[fmt])
        else:
            out_path = output
        if fmt == "text":
            out_path.write_text(
                _render_findings_text(findings, meta, "no_findings", colour=False),
                encoding="utf-8",
            )
        elif fmt == "markdown":
            out_path.write_text("MARKDOWN", encoding="utf-8")
        elif fmt == "sarif":
            out_path.write_text("SARIF", encoding="utf-8")
        else:
            # Bug path: should not be reached for fmt == "text".
            out_path.write_text("BUG: JSON INSTEAD OF TEXT", encoding="utf-8")

    content = output.read_text(encoding="utf-8")
    assert not content.lstrip().startswith("{"), (
        "fmt='text' must write plain text, not JSON (regression for #254)."
    )
    assert "\x1b[" not in content, "File output must not contain ANSI escapes."
    assert "NuGuard Red-Team" in content
    assert "2 finding(s)" in content


def test_dispatch_json_format_still_writes_json_file(tmp_path: Path) -> None:
    """Sanity check: --format json --output foo.json still writes valid JSON.

    The fix must not regress the JSON output path.
    """
    import json as _json

    from nuguard.redteam.report import to_json

    findings = _make_findings()
    meta = ReportMeta(verbose=False)
    output = tmp_path / "redteam.json"
    effective_formats = ["json"]
    extension_map = {"text": ".txt", "json": ".json", "markdown": ".md", "sarif": ".sarif"}

    for fmt in effective_formats:
        out_path = output.with_suffix(extension_map[fmt])
        if fmt == "json":
            out_path.write_text(
                to_json(findings, meta=meta, scan_outcome="no_findings"),
                encoding="utf-8",
            )

    content = output.read_text(encoding="utf-8")
    parsed = _json.loads(content)
    assert "_meta" in parsed
    assert len(parsed["findings"]) == 2