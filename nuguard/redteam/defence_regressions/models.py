"""Pydantic models for the defence-regression evaluator (W5)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

VariantKind = Literal[
    "literal",
    "roleplay",
    "extraction_markers",
    "audit_evidence",
    "encoded",
    "second_person_indirection",
]


class DefenceRegressionSpec(BaseModel):
    """One ``redteam.defence_regressions`` entry from ``nuguard.yaml``."""

    name: str
    message: str
    expect: Literal["refused"] = "refused"
    severity: Literal["critical", "high", "medium", "low", "info"] = "high"

    @classmethod
    def from_config_dict(cls, raw: dict) -> "DefenceRegressionSpec | None":
        """Build a spec from one ``redteam_defence_regressions`` dict entry.

        Returns ``None`` (rather than raising) for a malformed entry so one
        bad config row does not abort the whole evaluator — callers should
        log and skip.
        """
        if not isinstance(raw, dict):
            return None
        name = raw.get("name")
        message = raw.get("message")
        if not name or not message:
            return None
        return cls(
            name=str(name),
            message=str(message),
            expect=raw.get("expect", "refused"),
            severity=raw.get("severity", "high"),
        )


class DefenceRegressionVariant(BaseModel):
    """One concrete message sent to the target for a given spec."""

    parent_name: str
    variant_kind: VariantKind
    message: str
    generated_by: Literal["builtin_template", "llm"] = "builtin_template"


class DefenceRegressionResult(BaseModel):
    """Outcome of sending one :class:`DefenceRegressionVariant`."""

    variant: DefenceRegressionVariant
    severity: Literal["critical", "high", "medium", "low", "info"] = "high"
    refused: bool
    response_excerpt: str = ""
    evidence: str = ""
    # Set when the send itself failed (network/transient) rather than
    # produced a judgeable response — callers should not treat this as a
    # confirmed bypass, only as an inconclusive probe.
    send_error: str | None = None

    @property
    def is_regression_hit(self) -> bool:
        """True when the app did NOT refuse a message that must always be refused."""
        return self.send_error is None and not self.refused


class DefenceRegressionRunSummary(BaseModel):
    """Aggregate coverage metric reported alongside findings (§8 success metric)."""

    specs_evaluated: int = 0
    variants_evaluated: int = 0
    variants_refused: int = 0

    @property
    def paraphrase_coverage(self) -> float:
        """Fraction of evaluated variants that were correctly refused, in [0, 1]."""
        if self.variants_evaluated == 0:
            return 1.0
        return self.variants_refused / self.variants_evaluated
