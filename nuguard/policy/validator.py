"""Cognitive Policy linter.

Validates a parsed CognitivePolicy for completeness and common mistakes.
Returns a list of LintIssue dataclass instances; never raises.
"""

from __future__ import annotations

from dataclasses import dataclass

from nuguard.common.logging import get_logger
from nuguard.models.policy import CognitivePolicy

_log = get_logger(__name__)


@dataclass
class LintIssue:
    """A single linting finding produced by lint_policy."""

    rule_id: str
    message: str
    severity: str  # "error" | "warning" | "info"


def lint_policy(policy: CognitivePolicy) -> list[LintIssue]:
    """Return a list of LintIssues for *policy*.

    Rules
    -----
    POLICY-001 (warning)  No allowed_topics AND no restricted_topics.
    POLICY-002 (warning)  hitl_triggers present but no restricted_actions.
    POLICY-003 (warning)  A hitl_trigger entry is shorter than 10 characters.
    POLICY-004 (error)    A rate_limit value is <= 0.
    POLICY-005 (error)    All sections are empty (policy is effectively blank).
    POLICY-006 (warning)  Duplicate entries within any single section.
    """
    issues: list[LintIssue] = []

    # ---- POLICY-001 -------------------------------------------------------
    if not policy.allowed_topics and not policy.restricted_topics:
        issues.append(
            LintIssue(
                rule_id="POLICY-001",
                message=(
                    "Policy defines neither allowed_topics nor restricted_topics. "
                    "At least one set should be specified to constrain agent behaviour."
                ),
                severity="warning",
            )
        )

    # ---- POLICY-002 -------------------------------------------------------
    if policy.hitl_triggers and not policy.restricted_actions:
        issues.append(
            LintIssue(
                rule_id="POLICY-002",
                message=(
                    "hitl_triggers are defined but restricted_actions is empty. "
                    "Consider listing the actions that require human approval."
                ),
                severity="warning",
            )
        )

    # ---- POLICY-003 -------------------------------------------------------
    for trigger in policy.hitl_triggers:
        if len(trigger) < 10:
            issues.append(
                LintIssue(
                    rule_id="POLICY-003",
                    message=(
                        f"hitl_trigger entry {trigger!r} is shorter than 10 characters "
                        "and may be too vague to reliably match trigger conditions."
                    ),
                    severity="warning",
                )
            )

    # ---- POLICY-004 -------------------------------------------------------
    for key, val in policy.rate_limits.items():
        if val <= 0:
            issues.append(
                LintIssue(
                    rule_id="POLICY-004",
                    message=(
                        f"rate_limits[{key!r}] = {val} is <= 0, which is not a valid limit."
                    ),
                    severity="error",
                )
            )

    # ---- POLICY-005 -------------------------------------------------------
    all_sections = [
        policy.allowed_topics,
        policy.restricted_topics,
        policy.restricted_actions,
        policy.hitl_triggers,
        policy.data_classification,
        list(policy.rate_limits),
    ]
    if all(not s for s in all_sections):
        issues.append(
            LintIssue(
                rule_id="POLICY-005",
                message="All policy sections are empty. The policy has no effect.",
                severity="error",
            )
        )

    # ---- POLICY-006 -------------------------------------------------------
    sections_to_check: dict[str, list[str]] = {
        "allowed_topics": policy.allowed_topics,
        "restricted_topics": policy.restricted_topics,
        "restricted_actions": policy.restricted_actions,
        "hitl_triggers": policy.hitl_triggers,
        "data_classification": policy.data_classification,
    }
    for section_name, items in sections_to_check.items():
        seen: set[str] = set()
        for item in items:
            normalised = item.strip().lower()
            if normalised in seen:
                issues.append(
                    LintIssue(
                        rule_id="POLICY-006",
                        message=(
                            f"Duplicate entry {item!r} in section {section_name!r}."
                        ),
                        severity="warning",
                    )
                )
            seen.add(normalised)

    _log.debug("lint_policy found %d issues", len(issues))
    return issues
