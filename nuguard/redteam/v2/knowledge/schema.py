"""``TechniqueRecord`` — the versioned knowledge-base unit for the v2 engine.

A technique record is *metadata + safe strategy*; it never stores raw harmful
payloads (see the design's non-negotiable safety rules).  Concrete prompts are
synthesised at runtime by the catalog builder referenced via ``builder_key``.

Records add what a v1 :class:`~nuguard.redteam.catalog.spec.ScenarioSpec` lacks:
source traceability (``source_refs``), explicit scheduler ``execution`` metadata
(phase / state impact / locks / fixtures / reset hooks), a closed ``detectors``
set, and ``mapped_controls`` that findings can validate against.

The closed taxonomy enums (:class:`Detector`, :class:`StateImpact`) follow the
same philosophy as :mod:`nuguard.redteam.catalog.taxonomy` — ``str`` enums that
serialise as plain strings yet fail fast on a typo at load time.  ``evidence_types``
and ``safe_execution`` are reused directly from the catalog taxonomy.
"""
from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nuguard.redteam.catalog.taxonomy import EvidenceType, SafeExecution

_ID_RE = re.compile(r"^AIT-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{3}$")


class Detector(str, Enum):
    """Detection signals the layered evaluator can assert against a result.

    Distinct from :class:`~nuguard.redteam.catalog.taxonomy.EvidenceType`: an
    evidence type is the *artifact* captured (e.g. ``tool_trace``); a detector is
    the *verdict signal* derived from artifacts (e.g. ``forbidden_tool_call``).
    """

    CANARY_LEAK = "canary_leak"
    PII_DISCLOSURE = "pii_disclosure"
    SECRET_DISCLOSURE = "secret_disclosure"
    SYSTEM_PROMPT_LEAK = "system_prompt_leak"
    FORBIDDEN_TOOL_CALL = "forbidden_tool_call"
    TOOL_ARG_POLICY_VIOLATION = "tool_arg_policy_violation"
    MISSING_APPROVAL = "missing_approval"
    AUTHZ_MISMATCH = "authz_mismatch"
    EGRESS_HIT = "egress_hit"
    ROLE_SCOPE_MISMATCH = "role_scope_mismatch"
    JUDGE_POLICY_VIOLATION = "judge_policy_violation"
    SIDE_EFFECT_CONFIRMED = "side_effect_confirmed"
    REFUSAL_ABSENT = "refusal_absent"


class StateImpact(str, Enum):
    """How much target/fixture state a technique mutates when executed.

    Drives scheduler ordering and dry-run safety: anything above ``fixture_write``
    must be gated behind dry-run/emulated tools (``redteam.v2.dry_run_only``).
    """

    NONE = "none"                    # read-only / pure conversation
    FIXTURE_WRITE = "fixture_write"  # writes NuGuard-owned synthetic fixtures
    MEMORY_WRITE = "memory_write"    # persists into agent memory/profile
    EXTERNAL_WRITE = "external_write"  # external send/egress (canary/trap only)
    DESTRUCTIVE = "destructive"      # delete/purchase/refund/account mutation


class ExecutionMeta(BaseModel):
    """Scheduler-facing execution metadata for a technique."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    phase: int = Field(ge=0, le=10, description="Scheduler phase 0-10 (see scheduler/phases.py).")
    state_impact: StateImpact = StateImpact.NONE
    resource_locks: tuple[str, ...] = ()
    required_fixtures: tuple[str, ...] = ()
    reset_hooks: tuple[str, ...] = ()


class TechniqueRecord(BaseModel):
    """A single versioned red-team technique record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(description="Stable ID, e.g. 'AIT-INDIRECT-INJECTION-001'.")
    name: str
    family: str = Field(description="Technique family key from the design's family table.")
    attack_intent: str = Field(description="What the technique is trying to achieve, in one line.")
    source_refs: tuple[str, ...] = Field(description="Standards/academic/vendor references.")
    surfaces: tuple[str, ...] = Field(description="SBOM/attack-surface entry points this applies to.")
    preconditions: tuple[str, ...] = ()
    safe_payload_strategy: tuple[str, ...] = Field(
        description="How to synthesise a SAFE payload at runtime (no raw harmful content)."
    )
    success_criteria: tuple[str, ...] = Field(description="Behaviour-level outcomes that count as success.")
    detectors: tuple[Detector, ...] = Field(description="Verdict signals the evaluator should assert.")
    evidence_types: tuple[EvidenceType, ...] = ()
    safe_execution: SafeExecution = SafeExecution.CANARY_ONLY
    execution: ExecutionMeta
    mapped_controls: tuple[str, ...] = Field(
        default=(), description="Defensive controls a finding validates (from the design's control list)."
    )
    # Cross-links into the v1 catalog for payload synthesis + scenario coverage.
    builder_key: str | None = Field(
        default=None,
        description="Catalog BUILDER_FACTORIES key used to synthesise payloads. None = strategy-only.",
    )
    mapped_scenario_ids: tuple[str, ...] = Field(
        default=(), description="Catalog ScenarioSpec IDs (e.g. 'D01') this technique covers."
    )
    owasp_llm: tuple[str, ...] = ()
    owasp_agentic: tuple[str, ...] = ()
    mitre_atlas: tuple[str, ...] = ()

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(
                f"technique id {v!r} must match 'AIT-<UPPER-SEGMENTS>-NNN' "
                "(e.g. 'AIT-INDIRECT-INJECTION-001')"
            )
        return v

    @field_validator("source_refs", "surfaces", "safe_payload_strategy", "success_criteria", "detectors")
    @classmethod
    def _non_empty(cls, v: tuple) -> tuple:
        if not v:
            raise ValueError("field must be non-empty")
        return v
