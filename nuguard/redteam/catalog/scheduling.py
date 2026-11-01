"""Campaign scheduling metadata, kept beside (not inside) the scenario catalog.

:class:`~nuguard.redteam.catalog.spec.ScenarioSpec` stays a stable, snapshot-tested
description of *what* a scenario tests. This sidecar adds the *when/how* the
campaign scheduler needs — complexity levels, setup prerequisites, session policy
— keyed by catalog ID, so campaign mode can evolve without touching the catalog
snapshot or the legacy engine.

Levels follow ``documentation/developer-specs/redteam-v5.md`` §3. They are a
preferred order for breadth, never success gates: a refusal at one level does not
block another.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .spec import ScenarioSpec
from .taxonomy import DeliveryChannel, SafeExecution, ScenarioCategory

LEVELS: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6, 7)


class SessionPolicy(str, Enum):
    """How an objective may share a conversation branch."""

    REUSE = "reuse"        # may continue a compatible warm branch
    FRESH = "fresh"        # needs a clean branch (history-sensitive)
    ISOLATED = "isolated"  # needs its own disposable branch (persistent/side effects)


class Fixture(str, Enum):
    """Containment/fixture an objective needs before it may run."""

    NONE = ""
    SECOND_PRINCIPAL = "second_principal"   # synthetic_tenant
    CALLBACK_SERVER = "callback_server"     # trap_endpoint
    DECLARED_FIXTURE = "declared_fixture"   # dry_run_tool / sandbox / emulated_tool


@dataclass(frozen=True)
class SchedulingMeta:
    """Scheduling attributes of one catalog entry."""

    levels: tuple[int, ...]
    prerequisites: tuple[str, ...] = ("L0",)  # "L<n>" baseline levels or catalog IDs
    session_policy: SessionPolicy = SessionPolicy.REUSE
    contamination_tags: tuple[str, ...] = ()  # what an accepted attack taints on a branch
    technique_class: str = ""
    control_id: str = ""
    resource_scope: str = "chat"              # chat | api | tool | memory | external
    requires_fixture: Fixture = Fixture.NONE

    @property
    def primary_level(self) -> int:
        return min(self.levels)


def _ids(prefix: str, *nums: int) -> list[str]:
    return [f"{prefix}{n:02d}" for n in nums]


def _rng(prefix: str, lo: int, hi: int) -> list[str]:
    return _ids(prefix, *range(lo, hi + 1))


# Spec §3 table: level -> catalog IDs (an ID in several rows gets several levels).
_LEVEL_ROWS: dict[int, list[str]] = {
    1: ["E05", "E06", "M07", "S08"],
    2: ["J03", "J04", "J06", "E04"],
    3: ["B02", "B04", "B06", "E01", "E02", "E03", "J01", "J02", "J05"],
    4: (
        _rng("D", 1, 8)
        + ["A01", "A04", "A05", "N02", "N05", "S01", "S03", "S04", "S06"]
        + _rng("C", 1, 8)
    ),
    5: ["A03", "A06", "A07", "M05", "M06", "O02", "O03", "O04"] + _rng("H", 1, 5) + _rng("T", 1, 8),
    6: (
        _rng("I", 1, 8)
        + ["M01", "M02", "M03", "M04", "M08", "N04", "N06"]
        + _rng("P", 1, 6)
        + _rng("R", 1, 8)
        + _rng("G", 1, 6)
    ),
    7: _rng("K", 1, 6) + _rng("V", 1, 7) + ["G05", "G06", "T07", "T08"],
}

# Entries the spec table does not name: assigned from registry metadata.
_CATEGORY_DEFAULT_LEVEL: dict[ScenarioCategory, int] = {
    ScenarioCategory.AUTHORIZATION: 4,
    ScenarioCategory.API_SCHEMA: 4,
    ScenarioCategory.AGENT_IDENTITY: 4,
    ScenarioCategory.EVASION: 3,
    ScenarioCategory.BUSINESS_LOGIC: 3,
    ScenarioCategory.IMPROPER_OUTPUT: 3,
    ScenarioCategory.DUAL_PATH_EXPOSURE: 5,
    ScenarioCategory.ROUTER_SELECTION: 6,
}
_FALLBACK_LEVEL = 5

_INSTRUCTION_OVERRIDE = frozenset(["J03", "J04", "J06", "E04"])
_ISOLATED_PREFIXES = ("T", "P", "K", "V")
_FRESH_PREFIXES = ("M", "R", "G", "I")


def _fixture_for(safe: SafeExecution) -> Fixture:
    if safe == SafeExecution.SYNTHETIC_TENANT:
        return Fixture.SECOND_PRINCIPAL
    if safe == SafeExecution.TRAP_ENDPOINT:
        return Fixture.CALLBACK_SERVER
    if safe in (SafeExecution.DRY_RUN_TOOL, SafeExecution.SANDBOX, SafeExecution.EMULATED_TOOL):
        return Fixture.DECLARED_FIXTURE
    return Fixture.NONE


def _scope_for(channel: DeliveryChannel) -> str:
    if channel == DeliveryChannel.API:
        return "api"
    if channel in (DeliveryChannel.MEMORY, DeliveryChannel.MULTI_SESSION):
        return "memory"
    if channel in (DeliveryChannel.TOOL_OUTPUT, DeliveryChannel.MCP_METADATA):
        return "tool"
    if channel == DeliveryChannel.USER_PROMPT:
        return "chat"
    return "external"


def _levels_for(spec: ScenarioSpec) -> tuple[int, ...]:
    listed = sorted(lvl for lvl, ids in _LEVEL_ROWS.items() if spec.id in ids)
    if listed:
        return tuple(listed)
    return (_CATEGORY_DEFAULT_LEVEL.get(spec.category, _FALLBACK_LEVEL),)


def _policy_for(spec: ScenarioSpec) -> SessionPolicy:
    if spec.id[0] in _ISOLATED_PREFIXES:
        return SessionPolicy.ISOLATED
    if spec.id[0] in _FRESH_PREFIXES:
        return SessionPolicy.FRESH
    return SessionPolicy.REUSE


def scheduling_for(spec: ScenarioSpec) -> SchedulingMeta:
    """Return the scheduling metadata for *spec* (explicit table, else derived).

    Legacy scenarios without a catalog ID never reach this function; the
    scheduler falls back to a ``ScenarioType``-keyed level for them.
    """
    return SchedulingMeta(
        levels=_levels_for(spec),
        session_policy=_policy_for(spec),
        contamination_tags=("instruction_override",) if spec.id in _INSTRUCTION_OVERRIDE else (),
        technique_class=spec.scenario_type.value,
        control_id=spec.category.value,
        resource_scope=_scope_for(spec.delivery_channel),
        requires_fixture=_fixture_for(spec.safe_execution),
    )


def build_scheduling_table(catalog: Iterable[ScenarioSpec]) -> dict[str, SchedulingMeta]:
    """Scheduling metadata for every *enabled* spec in *catalog*."""
    return {s.id: scheduling_for(s) for s in catalog if s.enabled}


@dataclass(frozen=True)
class AvailableFixtures:
    """Containment the operator/run has actually provisioned."""

    second_principal: bool = False
    callback_server: bool = False
    declared_fixture: bool = False

    def provides(self, needed: Fixture) -> bool:
        if needed == Fixture.NONE:
            return True
        return bool(getattr(self, needed.value))


def blocked_fixture_reason(meta: SchedulingMeta, available: AvailableFixtures) -> str | None:
    """``"blocked_fixture:<needed>"`` when containment is missing, else ``None``.

    A natural-language "do not execute" request is not containment: without the
    declared fixture the objective is blocked, not run.
    """
    if available.provides(meta.requires_fixture):
        return None
    return f"blocked_fixture:{meta.requires_fixture.value}"
