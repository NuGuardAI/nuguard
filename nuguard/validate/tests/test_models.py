"""Tests for nuguard.models.validate."""
from __future__ import annotations

import pytest

from nuguard.models.validate import (
    CapabilityEntry,
    CapabilityMap,
    TurnPolicyRecord,
    ValidateFindingType,
    ValidateRunResult,
    ValidateScenario,
    ValidateScenarioType,
)


def test_capability_map_computed_fields():
    cm = CapabilityMap(
        run_id="test-1",
        entries=[
            CapabilityEntry(tool_name="search", exercised=True),
            CapabilityEntry(tool_name="schedule", exercised=False),
            CapabilityEntry(tool_name="book", exercised=True),
        ],
    )
    assert cm.tools_total == 3
    assert cm.tools_exercised == 2


def test_capability_map_diff_no_regression():
    baseline = CapabilityMap(
        run_id="base",
        entries=[CapabilityEntry(tool_name="search", exercised=True, policy_compliant=True)],
    )
    current = CapabilityMap(
        run_id="current",
        entries=[CapabilityEntry(tool_name="search", exercised=True, policy_compliant=True)],
    )
    assert CapabilityMap.diff(baseline, current) == []


def test_capability_map_diff_tool_no_longer_exercised():
    baseline = CapabilityMap(
        run_id="base",
        entries=[CapabilityEntry(tool_name="search", exercised=True, policy_compliant=True)],
    )
    current = CapabilityMap(
        run_id="current",
        entries=[CapabilityEntry(tool_name="search", exercised=False, policy_compliant=True)],
    )
    regressions = CapabilityMap.diff(baseline, current)
    assert len(regressions) == 1
    assert "search" in regressions[0]
    assert "not exercised" in regressions[0]


def test_capability_map_diff_tool_removed():
    baseline = CapabilityMap(
        run_id="base",
        entries=[CapabilityEntry(tool_name="search", exercised=True, policy_compliant=True)],
    )
    current = CapabilityMap(run_id="current", entries=[])
    regressions = CapabilityMap.diff(baseline, current)
    assert len(regressions) == 1
    assert "search" in regressions[0]
    assert "no longer in" in regressions[0]


def test_capability_map_diff_policy_regression():
    baseline = CapabilityMap(
        run_id="base",
        entries=[CapabilityEntry(tool_name="search", exercised=True, policy_compliant=True)],
    )
    current = CapabilityMap(
        run_id="current",
        entries=[CapabilityEntry(tool_name="search", exercised=True, policy_compliant=False)],
    )
    regressions = CapabilityMap.diff(baseline, current)
    assert len(regressions) == 1
    assert "policy" in regressions[0]


def test_turn_policy_record_passed_flag():
    record = TurnPolicyRecord(turn=1, prompt="hello", response="hi", passed=True)
    assert record.passed is True


def test_validate_finding_type_values():
    assert ValidateFindingType.CAPABILITY_GAP.value == "CAPABILITY_GAP"
    assert ValidateFindingType.BOUNDARY_FAILURE.value == "BOUNDARY_FAILURE"
    assert ValidateFindingType.POLICY_VIOLATION.value == "POLICY_VIOLATION"
    assert ValidateFindingType.CAPABILITY_REGRESSION.value == "CAPABILITY_REGRESSION"


def test_validate_run_result_empty():
    result = ValidateRunResult(
        run_id="test",
        capability_map=CapabilityMap(run_id="test"),
    )
    assert result.scan_outcome == "no_findings"
    assert result.findings == []
    assert result.scenarios_executed == 0
