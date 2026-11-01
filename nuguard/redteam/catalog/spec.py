"""The :class:`ScenarioSpec` declarative unit — one per catalog ID.

A spec carries *metadata + selection criteria*; it does not produce payloads.
At generation time the selector matches ``required_capabilities`` against the
target's :class:`AppCapabilityProfile`, then calls the factory referenced by
``builder_key`` to synthesise concrete ``AttackScenario`` objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from nuguard.models.exploit_chain import GoalType, ScenarioType

from .taxonomy import (
    Capability,
    DeliveryChannel,
    EvidenceType,
    SafeExecution,
    ScenarioCategory,
    SinkType,
    SourceTrust,
)


@dataclass(frozen=True)
class ScenarioSpec:
    """A single catalog scenario specification (stable, snapshot-tested)."""

    id: str                              # "D01", "C03" — stable, never reused
    category: ScenarioCategory
    title: str
    goal_type: GoalType
    scenario_type: ScenarioType
    delivery_channel: DeliveryChannel
    source_trust: SourceTrust
    sink_type: SinkType
    required_capabilities: frozenset[Capability]
    evidence_types: tuple[EvidenceType, ...]
    safe_execution: SafeExecution
    expected_control: str
    success_signal: str
    owasp_llm: tuple[str, ...] = ()
    owasp_agentic: tuple[str, ...] = ()
    base_impact: float = 5.0
    # Key into BUILDER_FACTORIES.  Empty → defaults to ``id`` at registry build.
    builder_key: str = ""
    # When False the spec is registered but never emitted (placeholder for a
    # series whose builder is not yet wired).  Coverage records it as such.
    enabled: bool = True
    # Which of the doc's six prioritisation rules apply (1-6); used for ranking.
    priority_rules: tuple[int, ...] = field(default_factory=tuple)
    # ── redteam-proposal.md flags (additive, all default to "no new behavior") ──
    # Eligible for W2 TrustContext re-execution (credential/identity-binding
    # matrix) once that executor wrapper lands. Set on families the proposal
    # calls out as identity-sensitive: D02/D03, A-series, G-series.
    identity_sensitive: bool = False
    # Opt-out for scenarios where mutating the payload breaks the test (e.g.
    # an exact canary-match string). True by default; the W6 decorator hook
    # checks this before trying an encoding/framing mutation on failure.
    decorator_allowed: bool = True
    # Has a direct-invocation counterpart (W10) alongside the chat-mediated
    # step — the X-series pairing.
    dual_path: bool = False
    # Scheduled alongside normal batches rather than through the per-scenario
    # semaphore slot — opens a passive tap once per batch instead (W7's
    # observation-channel scenarios).
    observation_parallel: bool = False

    def resolved_builder_key(self) -> str:
        return self.builder_key or self.id
