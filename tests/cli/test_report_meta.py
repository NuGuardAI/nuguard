from __future__ import annotations

from nuguard.cli.report_meta import ReportMeta


def test_report_meta_includes_finding_triggers_in_dict() -> None:
    meta = ReportMeta(
        timestamp="2026-03-31T00:00:00+00:00",
        finding_triggers={
            "canary_hits": True,
            "policy_violations": False,
            "critical_success_hits": True,
            "any_inject_success": False,
        },
    )

    payload = meta.to_dict()
    assert "finding_triggers" in payload
    assert payload["finding_triggers"]["policy_violations"] is False


def test_report_meta_renders_trigger_summary_for_markdown_and_text() -> None:
    meta = ReportMeta(
        timestamp="2026-03-31T00:00:00+00:00",
        finding_triggers={
            "canary_hits": True,
            "policy_violations": True,
            "critical_success_hits": False,
            "any_inject_success": True,
        },
    )

    markdown = "\n".join(meta.to_markdown_lines())
    text_line = meta.to_text_line()

    assert "Finding Triggers:" in markdown
    assert "critical_success_hits=off" in markdown
    assert "Triggers:" in text_line
    assert "any_inject_success=on" in text_line
