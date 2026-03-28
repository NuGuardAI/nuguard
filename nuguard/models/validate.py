"""Validate-mode models: scenarios, capability map, and run results."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field, computed_field


class ValidateScenarioType(str, Enum):
    CAPABILITY_PROBE = "capability_probe"
    HAPPY_PATH = "happy_path"
    BOUNDARY_ASSERTION = "boundary_assertion"
    POLICY_COMPLIANCE = "policy_compliance"


class ValidateFindingType(str, Enum):
    CAPABILITY_GAP = "CAPABILITY_GAP"
    CAPABILITY_REGRESSION = "CAPABILITY_REGRESSION"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    BOUNDARY_FAILURE = "BOUNDARY_FAILURE"


class TurnPolicyRecord(BaseModel):
    """Per-turn record capturing policy and canary evaluation results."""

    turn: int
    prompt: str
    response: str
    tool_calls: list[dict] = Field(default_factory=list)
    violations: list[dict] = Field(default_factory=list)  # serialised PolicyViolation dicts
    canary_hits: list[str] = Field(default_factory=list)
    passed: bool = True  # False when any violation or canary_hit is present


class ValidateScenario(BaseModel):
    """A single runnable validate scenario."""

    scenario_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    scenario_type: ValidateScenarioType
    name: str
    messages: list[str] = Field(default_factory=list)
    expected_tool: str | None = None       # for CAPABILITY_PROBE
    expect_refused: bool = False           # for BOUNDARY_ASSERTION
    forbid_pattern: str = ""              # for BOUNDARY_ASSERTION
    policy_clauses: list[str] = Field(default_factory=list)  # for POLICY_COMPLIANCE


class CapabilityEntry(BaseModel):
    """Whether a declared tool was exercised during validate."""

    tool_name: str
    exercised: bool = False
    exercised_by: str | None = None   # "scenario_type/scenario_name"
    calls_observed: int = 0
    policy_compliant: bool = True


class CapabilityMap(BaseModel):
    """Snapshot of tool coverage and compliance from a validate run."""

    run_id: str
    built_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    entries: list[CapabilityEntry] = Field(default_factory=list)

    @computed_field  # type: ignore[misc]
    @property
    def tools_exercised(self) -> int:
        return sum(1 for e in self.entries if e.exercised)

    @computed_field  # type: ignore[misc]
    @property
    def tools_total(self) -> int:
        return len(self.entries)

    @classmethod
    def diff(cls, baseline: "CapabilityMap", current: "CapabilityMap") -> list[str]:
        """Return regression descriptions for tools that regressed since baseline."""
        baseline_ok = {e.tool_name for e in baseline.entries if e.exercised and e.policy_compliant}
        current_ok = {e.tool_name for e in current.entries if e.exercised and e.policy_compliant}
        regressions: list[str] = []
        for tool in sorted(baseline_ok - current_ok):
            current_entry = next((e for e in current.entries if e.tool_name == tool), None)
            if current_entry is None:
                regressions.append(f"{tool}: tool no longer in capability map")
            elif not current_entry.exercised:
                regressions.append(f"{tool}: was exercised in baseline, not exercised now")
            else:
                regressions.append(f"{tool}: was policy-compliant in baseline, policy violation now")
        return regressions


class ValidateRunResult(BaseModel):
    """Complete result of a validate run."""

    run_id: str
    findings: list[dict] = Field(default_factory=list)  # serialised Finding dicts
    capability_map: CapabilityMap
    policy_records: list[TurnPolicyRecord] = Field(default_factory=list)
    scenarios_executed: int = 0
    scan_outcome: str = "no_findings"
