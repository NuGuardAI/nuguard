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


def _is_placeholder_remediation(remediation: str, description: Any) -> bool:
    """True when *remediation* is a low-value stand-in, not real guidance.

    A finding can arrive with ``remediation`` already set to a copy (or
    truncated copy) of its own ``description`` — e.g. an upstream trigger
    that stamped the scenario/violation summary into both fields. That's not
    actionable "how to fix it" text, so it should not block the synthesizer's
    grounded ``RemediationArtefact`` from ever being applied.
    """
    if not remediation:
        return True
    desc = str(description or "")
    if not desc:
        return False
    return remediation == desc or desc.startswith(remediation)


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
    fallback: str | None = FALLBACK_REMEDIATION_TEXT,
) -> None:
    """Mutate *findings* in place, setting each finding's flat ``remediation``.

    For each finding, picks the best-matching artefact (matched via
    ``RemediationArtefact.finding_ids`` membership, preferring
    ``SYSTEM_PROMPT_PATCH`` artefacts when a finding matches more than one)
    and sets ``finding.remediation`` from that artefact's ``rationale``
    verbatim — no truncation; the LLM's own word budget (see
    ``nuguard.remediation.prompts.REMEDIATION_PERSONA``) is the only length
    constraint. Skips findings that already carry real, non-placeholder
    remediation text (see :func:`_is_placeholder_remediation`) but overwrites
    a placeholder (empty, or a copy/truncation of the finding's own
    ``description``) whenever a matching artefact exists. When a finding
    matches no artefact at all, sets *fallback* (pass ``None`` to leave the
    field untouched instead).

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
        existing = _get(finding, "remediation") or ""
        description = _get(finding, "description")
        if existing and not _is_placeholder_remediation(existing, description):
            continue
        finding_id = str(_get(finding, "finding_id") or "")
        matched_artefact = best_by_finding_id.get(finding_id)
        if matched_artefact is None:
            if not existing and fallback is not None:
                _set(finding, "remediation", fallback)
            continue
        # A merged artefact's own `rationale` is a joined blob of several
        # findings' text; using it directly for every finding_id in the
        # merge risks one finding's remediation drowning out a sibling's.
        # Prefer this finding's own preserved rationale when the artefact
        # was merged.
        own_rationale = matched_artefact.per_finding_rationale.get(finding_id)
        rationale = own_rationale if own_rationale else matched_artefact.rationale
        _set(finding, "remediation", rationale)
