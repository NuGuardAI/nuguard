"""Backfills the flat ``Finding.remediation`` string from synthesized artefacts.

``RemediationSynthesizer`` runs as an async, post-hoc pass after all findings
are collected, producing structured ``RemediationArtefact`` objects. This
module reconciles that structured output back onto each finding's flat
``remediation`` field (used for the inline "**Remediation:**" line in
reports) so both representations stay in sync without the old per-GoalType
template strings.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nuguard.models.finding import Finding
    from nuguard.remediation.models import RemediationArtefact

FALLBACK_REMEDIATION_TEXT = (
    "Automated remediation synthesis did not produce a specific fix for this "
    "finding — review the affected component manually and apply an "
    "appropriate guardrail, input validation, or system-prompt restriction."
)

# Lower number = preferred when a finding matches multiple artefact types.
# SYSTEM_PROMPT_PATCH artefacts carry the most directly actionable "what to
# do" text; guardrail/architectural artefacts are more structured/spec-like.
_ARTEFACT_TYPE_PRIORITY = {
    "system_prompt_patch": 0,
    "input_guardrail": 1,
    "output_guardrail": 1,
    "architectural_change": 2,
}


def _word_truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    last_space = truncated.rfind(" ")
    return truncated[:last_space].rstrip(".,;:") if last_space > 0 else truncated


def _get(finding: "Finding | dict[str, Any]", key: str) -> Any:
    if isinstance(finding, dict):
        return finding.get(key)
    return getattr(finding, key, None)


def _set(finding: "Finding | dict[str, Any]", key: str, value: Any) -> None:
    if isinstance(finding, dict):
        finding[key] = value
    else:
        setattr(finding, key, value)


def backfill_finding_remediation(
    findings: "list[Finding] | list[dict[str, Any]]",
    artefacts: "list[RemediationArtefact]",
    *,
    max_len: int = 320,
    fallback: str | None = FALLBACK_REMEDIATION_TEXT,
) -> None:
    """Mutate *findings* in place, setting each finding's flat ``remediation``.

    For each finding, picks the best-matching artefact (matched via
    ``RemediationArtefact.finding_ids`` membership, preferring
    ``SYSTEM_PROMPT_PATCH`` artefacts when a finding matches more than one)
    and sets ``finding.remediation`` from that artefact's ``rationale``,
    truncated to *max_len*. Never overwrites an already non-empty
    ``remediation`` value. When a finding matches no artefact at all, sets
    *fallback* (pass ``None`` to leave the field untouched instead).

    Works for both ``list[Finding]`` (redteam/analysis) and ``list[dict]``
    (behavior) inputs.
    """
    best_by_finding_id: dict[str, "RemediationArtefact"] = {}
    for artefact in artefacts:
        priority = _ARTEFACT_TYPE_PRIORITY.get(artefact.artefact_type.value, 99)
        for finding_id in artefact.finding_ids:
            current = best_by_finding_id.get(finding_id)
            if current is None:
                best_by_finding_id[finding_id] = artefact
                continue
            current_priority = _ARTEFACT_TYPE_PRIORITY.get(current.artefact_type.value, 99)
            if priority < current_priority:
                best_by_finding_id[finding_id] = artefact

    for finding in findings:
        if _get(finding, "remediation"):
            continue
        finding_id = str(_get(finding, "finding_id") or "")
        matched_artefact = best_by_finding_id.get(finding_id)
        if matched_artefact is None:
            if fallback is not None:
                _set(finding, "remediation", fallback)
            continue
        _set(finding, "remediation", _word_truncate(matched_artefact.rationale, max_len))
