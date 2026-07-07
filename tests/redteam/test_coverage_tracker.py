"""Tests for the CoverageTracker."""
from __future__ import annotations

from nuguard.redteam.coverage.tracker import CoverageTracker


class TestCoverageTracker:
    def test_record_generated(self) -> None:
        tracker = CoverageTracker()
        tracker.record_generated("node-1", "AGENT", "Main Agent")
        assert tracker.total_generated == 1
        assert "node-1" in tracker._nodes
        assert tracker._nodes["node-1"].generated == 1

    def test_record_generated_accumulates(self) -> None:
        tracker = CoverageTracker()
        tracker.record_generated("node-1", "AGENT", "Main Agent")
        tracker.record_generated("node-1", "AGENT", "Main Agent")
        assert tracker._nodes["node-1"].generated == 2
        assert tracker.total_generated == 2

    def test_record_executed(self) -> None:
        tracker = CoverageTracker()
        tracker.record_generated("node-1", "AGENT", "Agent")
        tracker.record_executed("node-1")
        assert tracker._nodes["node-1"].executed == 1
        assert tracker.total_executed == 1

    def test_record_executed_unknown_node_noop(self) -> None:
        tracker = CoverageTracker()
        tracker.record_executed("nonexistent")
        assert tracker.total_executed == 0

    def test_record_finding(self) -> None:
        tracker = CoverageTracker()
        tracker.record_generated("node-1", "AGENT", "Agent")
        tracker.record_finding("node-1")
        assert tracker._nodes["node-1"].findings == 1
        assert tracker.total_findings == 1

    def test_record_capped(self) -> None:
        tracker = CoverageTracker()
        tracker.record_capped()
        tracker.record_capped()
        assert tracker.capped_count == 2

    def test_to_markdown_empty(self) -> None:
        tracker = CoverageTracker()
        result = tracker.to_markdown()
        assert result == ""

    def test_to_markdown_with_data(self) -> None:
        tracker = CoverageTracker()
        tracker.record_generated("node-1", "AGENT", "Main Agent")
        tracker.record_executed("node-1")
        tracker.record_finding("node-1")
        md = tracker.to_markdown()
        assert "## SBOM Coverage" in md
        assert "Main Agent" in md
        assert "AGENT" in md

    def test_to_markdown_shows_capped_count(self) -> None:
        tracker = CoverageTracker()
        tracker.record_generated("node-1", "AGENT", "Agent")
        tracker.record_capped()
        tracker.record_capped()
        md = tracker.to_markdown()
        assert "2 additional scenario(s) were" in md
        assert "skipped due to per-goal agent caps" in md

    def test_to_markdown_no_capped_note_when_zero(self) -> None:
        tracker = CoverageTracker()
        tracker.record_generated("node-1", "AGENT", "Agent")
        md = tracker.to_markdown()
        assert "skipped due to per-goal" not in md

    def test_policy_clause_tracking(self) -> None:
        tracker = CoverageTracker()
        tracker.record_policy_clause("restricted_topics: weapons")
        assert tracker.total_generated == 1
        assert "restricted_topics: weapons" in tracker._policy_clauses

    def test_multiple_nodes(self) -> None:
        tracker = CoverageTracker()
        tracker.record_generated("n1", "AGENT", "Agent A")
        tracker.record_generated("n2", "TOOL", "Tool B")
        tracker.record_generated("n1", "AGENT", "Agent A")
        assert tracker.total_generated == 3
        assert len(tracker._nodes) == 2

    def test_to_dict_empty(self) -> None:
        tracker = CoverageTracker()
        assert tracker.to_dict() == {"nodes": [], "policy_clauses": [], "capped_count": 0}

    def test_to_dict_shape_is_json_safe(self) -> None:
        import json

        tracker = CoverageTracker()
        tracker.record_generated("n1", "AGENT", "Main Agent")
        tracker.record_executed("n1")
        tracker.record_finding("n1")
        tracker.record_policy_clause("restricted_topics: weapons")
        tracker.record_capped()

        d = tracker.to_dict()
        json.dumps(d)  # must not raise

        assert d["capped_count"] == 1
        assert d["nodes"] == [
            {
                "node_id": "n1",
                "node_type": "AGENT",
                "name": "Main Agent",
                "generated": 1,
                "executed": 1,
                "findings": 1,
                "skipped_reason": "",
            }
        ]
        assert d["policy_clauses"][0]["node_id"] == "restricted_topics: weapons"

    def test_to_dict_does_not_mutate_internal_state(self) -> None:
        tracker = CoverageTracker()
        tracker.record_generated("n1", "AGENT", "Agent")
        d = tracker.to_dict()
        d["nodes"][0]["generated"] = 999
        assert tracker._nodes["n1"].generated == 1
