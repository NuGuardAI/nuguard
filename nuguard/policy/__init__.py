"""Cognitive policy parsing, validation, compliance checking, and assessment.

Public API
----------
``parse_policy``
    Parse a Markdown policy document into a :class:`CognitivePolicy`.

``lint_policy``
    Validate a policy for completeness and common mistakes.

``check_policy_against_sbom``
    Cross-check a policy against an AiSbomDocument for structural gaps.

``run_compliance_assessment`` (async)
    Run a full framework compliance assessment against an AiSbomDocument.

``build_aibom_snapshot``
    Build a normalised assessment snapshot dict from an AiSbomDocument.

Models re-exported for convenience:
    CognitivePolicy, ComplianceControl, ControlEvaluation,
    PolicyAssessmentResult, ComplianceResult, ControlType,
    FrameworkRef, PolicyGap, LintIssue.

Note: ``build_aibom_snapshot``, ``check_policy_against_sbom``, and
``run_compliance_assessment`` are imported lazily (on first use) because they
depend on ``nuguard.sbom.models`` which may trigger optional heavy dependencies
(e.g. typescript parsers) at import time in some environments.
"""

from nuguard.models.policy import (
    ComplianceControl,
    ComplianceResult,
    ControlEvaluation,
    ControlType,
    CognitivePolicy,
    FrameworkRef,
    PolicyAssessmentResult,
)
from nuguard.policy.parser import parse_policy
from nuguard.policy.validator import LintIssue, lint_policy


def __getattr__(name: str):  # noqa: N807
    """Lazy import for symbols that pull in nuguard.sbom.models."""
    if name == "build_aibom_snapshot":
        from nuguard.policy.aibom_snapshot import build_aibom_snapshot  # noqa: PLC0415
        return build_aibom_snapshot
    if name == "check_policy_against_sbom":
        from nuguard.policy.checker import check_policy_against_sbom  # noqa: PLC0415
        return check_policy_against_sbom
    if name == "PolicyGap":
        from nuguard.policy.checker import PolicyGap  # noqa: PLC0415
        return PolicyGap
    if name == "PolicyControl":
        from nuguard.policy.checker import PolicyControl  # noqa: PLC0415
        return PolicyControl
    if name == "PolicyCheckResult":
        from nuguard.policy.checker import PolicyCheckResult  # noqa: PLC0415
        return PolicyCheckResult
    if name == "run_compliance_assessment":
        from nuguard.policy.assessment import run_compliance_assessment  # noqa: PLC0415
        return run_compliance_assessment
    raise AttributeError(f"module 'nuguard.policy' has no attribute {name!r}")


__all__ = [
    # Models
    "CognitivePolicy",
    "ComplianceControl",
    "ComplianceResult",
    "ControlEvaluation",
    "ControlType",
    "FrameworkRef",
    "LintIssue",
    "PolicyAssessmentResult",
    "PolicyGap",
    "PolicyControl",
    "PolicyCheckResult",
    # Functions
    "build_aibom_snapshot",
    "check_policy_against_sbom",
    "lint_policy",
    "parse_policy",
    "run_compliance_assessment",
]
