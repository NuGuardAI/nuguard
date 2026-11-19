from __future__ import annotations

from nuguard.redteam.coverage.tracker import CoverageTracker, render_coverage_markdown


def test_uncovered_gated_nodes_reported() -> None:
    t = CoverageTracker()
    t.set_expected([
        ("a1", "AGENT", "Plan"),
        ("a2", "AGENT", "Execute"),
        ("t1", "TOOL", "systemutils__execute_script"),
        ("t2", "TOOL", "list_vendors"),  # not high risk -> not gated
        ("d1", "DATASTORE", "Postgresql"),
    ])
    t.record_generated("a1", "AGENT", "Plan")
    t.record_executed("a1")
    t.record_generated("a2", "AGENT", "Execute")
    gaps = {g["node_id"]: g["reason"] for g in t.uncovered}
    assert set(gaps) == {"a2", "t1", "d1"}
    assert gaps["a2"] == "1 generated, none executed"
    assert gaps["t1"] == "no scenario generated"


def test_markdown_includes_gap_table() -> None:
    t = CoverageTracker()
    t.set_expected([("d1", "DATASTORE", "Redis")])
    md = render_coverage_markdown(t.to_dict())
    assert "Coverage gaps (1" in md and "| Redis | DATASTORE | no scenario generated |" in md
