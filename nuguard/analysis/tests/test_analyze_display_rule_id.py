"""Unit tests for human-friendly rule-id display in the markdown report.

Structural findings (NGA/ATLAS/semgrep/checkov) carry an internal
``finding_id`` of the form ``"<tool>-<rule_id>-<8-hex-dedup-suffix>"`` (e.g.
``nga-NGA-006-aacb1fe0``) — confusing when shown to users as-is. The report
should display just the rule id (``NGA-006``).
"""

from __future__ import annotations

from nuguard.cli.commands.analyze import _display_rule_id, _display_tool_name
from nuguard.models.finding import Finding, Severity


def _finding(finding_id: str) -> Finding:
    return Finding(finding_id=finding_id, title="t", severity=Severity.HIGH, description="d")


def test_strips_tool_prefix_and_dedup_hex_suffix() -> None:
    assert _display_rule_id(_finding("nga-NGA-006-aacb1fe0")) == "NGA-006"


def test_strips_for_atlas_and_semgrep_prefixes() -> None:
    assert _display_rule_id(_finding("atlas-NC-003-12ab34cd")) == "NC-003"
    assert _display_rule_id(_finding("semgrep-py.flask.injection-deadbeef")) == "py.flask.injection"


def test_falls_back_to_raw_id_when_pattern_does_not_match() -> None:
    assert _display_rule_id(_finding("custom-rule-id")) == "rule-id"


def test_nga_source_displays_as_nuguard_best_practices() -> None:
    assert _display_tool_name("nga") == "NuGuard Best Practices"
    assert _display_tool_name("nga-rules") == "NuGuard Best Practices"


def test_other_tool_names_pass_through_unchanged() -> None:
    for tool in ("trivy", "osv", "grype", "checkov", "semgrep", "atlas"):
        assert _display_tool_name(tool) == tool
