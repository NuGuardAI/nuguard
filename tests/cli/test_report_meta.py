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


def test_report_meta_renders_trigger_summary_for_text() -> None:
    meta = ReportMeta(
        timestamp="2026-03-31T00:00:00+00:00",
        finding_triggers={
            "canary_hits": True,
            "policy_violations": True,
            "critical_success_hits": False,
            "any_inject_success": True,
        },
    )

    text_line = meta.to_text_line()

    # Finding Triggers are rendered in the redteam report Summary section,
    # not in the header (to_markdown_lines). Verify the text line carries them.
    assert "Triggers:" in text_line
    assert "any_inject_success=on" in text_line
    assert "critical_success_hits=off" in text_line


def test_report_meta_markdown_lines_do_not_include_finding_triggers() -> None:
    meta = ReportMeta(
        timestamp="2026-03-31T00:00:00+00:00",
        finding_triggers={
            "canary_hits": True,
            "policy_violations": True,
        },
    )

    markdown = "\n".join(meta.to_markdown_lines())

    # Finding Triggers belong in the Summary section (redteam/report.py),
    # not in the metadata header produced by to_markdown_lines().
    assert "Finding Triggers" not in markdown


def test_report_meta_effective_endpoint_rendering() -> None:
    meta = ReportMeta(
        timestamp="2026-03-31T00:00:00+00:00",
        target_url="http://localhost:8080",
        target_endpoint="/chat",
        effective_endpoint="/api/agent",
        target_endpoint_source="probe",
    )

    markdown = "\n".join(meta.to_markdown_lines())
    assert "**Target:** `http://localhost:8080/api/agent`" in markdown
    assert "**Effective Endpoint:** `/api/agent` (source: probe)" in markdown


def test_report_meta_dict_includes_endpoint_provenance() -> None:
    meta = ReportMeta(
        timestamp="2026-03-31T00:00:00+00:00",
        target_url="http://localhost:8080",
        effective_endpoint="/api/agent",
        target_endpoint_source="sbom",
        endpoint_discovery_notes=["SBOM endpoint selected"],
    )

    payload = meta.to_dict()
    assert payload["effective_endpoint"] == "/api/agent"
    assert payload["target_endpoint_source"] == "sbom"
    assert payload["endpoint_discovery_notes"] == ["SBOM endpoint selected"]


def test_report_meta_run_id_nonempty_and_unique() -> None:
    """Every report gets a canonical, non-empty correlation ID."""
    a = ReportMeta(timestamp="2026-03-31T00:00:00+00:00")
    b = ReportMeta(timestamp="2026-03-31T00:00:00+00:00")
    assert a.run_id
    assert b.run_id
    assert a.run_id != b.run_id
    assert (
        ReportMeta(timestamp="2026-03-31T00:00:00+00:00", run_id="fixed").to_dict()["run_id"]
        == "fixed"
    )


def test_report_meta_hides_run_id_from_default_user_facing_output() -> None:
    """run_id must not leak into default Markdown/text reports (#327)."""
    meta = ReportMeta(timestamp="2026-03-31T00:00:00+00:00", verbose=False)
    markdown = "\n".join(meta.to_markdown_lines())
    text = meta.to_text_line()
    assert meta.run_id not in markdown
    assert meta.run_id not in text


def test_report_meta_shows_run_id_when_verbose() -> None:
    """The debug/verbose flag opts back into correlation-ID visibility."""
    meta = ReportMeta(timestamp="2026-03-31T00:00:00+00:00", verbose=True)
    markdown = "\n".join(meta.to_markdown_lines())
    text = meta.to_text_line()
    assert f"**Run ID:** `{meta.run_id}`" in markdown
    assert f"Run ID: {meta.run_id}" in text
