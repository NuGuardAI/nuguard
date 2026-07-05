"""Tests for Universal Safety Topic visibility in redteam Markdown reports.

Covers:
- _universal_safety_summary(): per-category tested/finding bullet summary
- _truncate_scenario_details() / _diagnostics_priority(): findings and
  universal-safety scenarios survive the verbose diagnostics cap ahead of
  unrelated lower-priority scenarios
"""
from __future__ import annotations

from nuguard.output.validation_report import ScenarioDetail
from nuguard.redteam.report import (
    _MAX_DIAG_SCENARIOS,
    _diagnostics_priority,
    _truncate_scenario_details,
    _universal_safety_summary,
)


def _record(title: str, chain_status: str = "completed", had_finding: bool = False) -> dict:
    return {"title": title, "chain_status": chain_status, "had_finding": had_finding}


class TestUniversalSafetySummary:
    def test_no_universal_safety_scenarios_returns_empty(self) -> None:
        records = [_record("Restricted Topic Probe — weapons — explicit")]
        assert _universal_safety_summary(records) == []

    def test_counts_tested_and_findings_per_category(self) -> None:
        records = [
            _record("Universal Safety Probe (sexual_content) — explicit"),
            _record("Universal Safety Probe (sexual_content) — implicit (curious)"),
            _record("Universal Safety Probe (sexual_content) — implicit (fiction)"),
            _record("Universal Safety Probe (violence) — explicit"),
            _record("Universal Safety Probe (violence) — implicit (curious)", chain_status="similar_miss"),
            _record("Universal Safety Probe (self_harm) — explicit", had_finding=True),
            _record("Universal Safety Probe (self_harm) — implicit (curious)", chain_status="skipped"),
            _record("Universal Safety Probe (self_harm) — implicit (fiction)", chain_status="aborted"),
        ]
        lines = _universal_safety_summary(records)
        text = "\n".join(lines)
        assert "sexual_content`: 3/3 tested, 0 finding(s)" in text
        assert "violence`: 1/2 tested, 0 finding(s)" in text
        assert "self_harm`: 1/3 tested, 1 finding(s)" in text

    def test_ignores_non_universal_safety_records(self) -> None:
        records = [
            _record("Restricted Topic Probe — off-topic — explicit"),
            _record("Universal Safety Probe (violence) — explicit"),
        ]
        lines = _universal_safety_summary(records)
        text = "\n".join(lines)
        assert "violence" in text
        assert "off-topic" not in text


def _detail(title: str, had_finding: bool = False, index: int = 0) -> ScenarioDetail:
    return ScenarioDetail(
        index=index,
        title=title,
        scenario_type="GUARDRAIL_BYPASS",
        goal_or_type="PROMPT_DRIVEN_THREAT",
        status="FINDING" if had_finding else "PASS",
        turns=[],
        had_finding=had_finding,
    )


class TestDiagnosticsPriority:
    def test_findings_rank_above_universal_safety_and_rest(self) -> None:
        finding = _detail("Some Finding Scenario", had_finding=True)
        universal = _detail("Universal Safety Probe (self_harm) — explicit")
        rest = _detail("Restricted Topic Probe — off-topic — explicit")
        assert _diagnostics_priority(finding) < _diagnostics_priority(universal)
        assert _diagnostics_priority(universal) < _diagnostics_priority(rest)

    def test_universal_safety_survives_truncation_past_positional_cutoff(self) -> None:
        # Fill past the cap with unrelated low-priority scenarios first, then
        # append universal-safety scenarios last (as generator.py does) —
        # mirrors the real bug where they landed at position ~114-122 of 234.
        filler = [
            _detail(f"Restricted Topic Probe — filler {i} — explicit", index=i)
            for i in range(_MAX_DIAG_SCENARIOS + 5)
        ]
        universal = [
            _detail(f"Universal Safety Probe (self_harm) — variant {i}", index=1000 + i)
            for i in range(3)
        ]
        details = filler + universal

        truncated = _truncate_scenario_details(details)

        assert len(truncated) == _MAX_DIAG_SCENARIOS
        truncated_titles = {sd.title for sd in truncated}
        for u in universal:
            assert u.title in truncated_titles

    def test_findings_always_survive_even_with_many_universal_safety_scenarios(self) -> None:
        universal = [
            _detail(f"Universal Safety Probe (violence) — variant {i}", index=i)
            for i in range(_MAX_DIAG_SCENARIOS + 5)
        ]
        finding = _detail("Critical Finding Scenario", had_finding=True, index=9999)
        details = universal + [finding]

        truncated = _truncate_scenario_details(details)

        assert len(truncated) == _MAX_DIAG_SCENARIOS
        assert any(sd.title == "Critical Finding Scenario" for sd in truncated)

    def test_stable_order_preserved_within_priority_tier(self) -> None:
        details = [
            _detail("Universal Safety Probe (sexual_content) — a", index=0),
            _detail("Universal Safety Probe (violence) — b", index=1),
            _detail("Universal Safety Probe (self_harm) — c", index=2),
        ]
        truncated = _truncate_scenario_details(details)
        assert [sd.title for sd in truncated] == [d.title for d in details]
