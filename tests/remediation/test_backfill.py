"""Tests for nuguard.remediation.backfill.backfill_finding_remediation."""
from __future__ import annotations

from nuguard.models.finding import Finding, Severity
from nuguard.remediation.backfill import (
    FALLBACK_REMEDIATION_TEXT,
    backfill_finding_remediation,
)
from nuguard.remediation.models import RemediationArtefact, RemediationArtefactType


def _artefact(
    finding_ids: list[str],
    *,
    artefact_type: RemediationArtefactType = RemediationArtefactType.SYSTEM_PROMPT_PATCH,
    rationale: str = "Because of the evidence.",
) -> RemediationArtefact:
    return RemediationArtefact(
        finding_ids=finding_ids,
        component="AgentX",
        component_type="AGENT",
        artefact_type=artefact_type,
        priority="high",
        rationale=rationale,
    )


def _finding(finding_id: str = "f1", remediation: str | None = None) -> Finding:
    return Finding(
        finding_id=finding_id,
        title="t",
        severity=Severity.HIGH,
        description="d",
        remediation=remediation,
    )


def test_backfill_sets_remediation_from_matching_artefact_rationale():
    finding = _finding("f1")
    artefact = _artefact(["f1"], rationale="Add an auth check before the tool call.")

    backfill_finding_remediation([finding], [artefact])

    assert finding.remediation == "Add an auth check before the tool call."


def test_backfill_uses_fallback_when_no_artefact_matches():
    finding = _finding("f1")

    backfill_finding_remediation([finding], [])

    assert finding.remediation == FALLBACK_REMEDIATION_TEXT


def test_backfill_fallback_none_leaves_field_untouched():
    finding = _finding("f1")

    backfill_finding_remediation([finding], [], fallback=None)

    assert finding.remediation is None


def test_backfill_never_overwrites_existing_remediation():
    finding = _finding("f1", remediation="Already set.")
    artefact = _artefact(["f1"], rationale="Different text.")

    backfill_finding_remediation([finding], [artefact])

    assert finding.remediation == "Already set."


def test_backfill_prefers_system_prompt_patch_over_guardrail_artefact():
    finding = _finding("f1")
    guardrail = _artefact(
        ["f1"], artefact_type=RemediationArtefactType.INPUT_GUARDRAIL, rationale="guardrail rationale"
    )
    patch = _artefact(
        ["f1"], artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH, rationale="patch rationale"
    )

    backfill_finding_remediation([finding], [guardrail, patch])

    assert finding.remediation == "patch rationale"


def test_backfill_truncates_long_rationale():
    finding = _finding("f1")
    long_rationale = "word " * 200
    artefact = _artefact(["f1"], rationale=long_rationale)

    backfill_finding_remediation([finding], [artefact], max_len=50)

    assert len(finding.remediation) <= 50


def test_backfill_works_with_dict_findings():
    finding = {"finding_id": "f1", "remediation": None}
    artefact = _artefact(["f1"], rationale="Dict-path rationale.")

    backfill_finding_remediation([finding], [artefact])

    assert finding["remediation"] == "Dict-path rationale."


def test_backfill_matches_by_finding_id_not_position():
    f1 = _finding("f1")
    f2 = _finding("f2")
    artefact_for_f2 = _artefact(["f2"], rationale="Only for f2.")

    backfill_finding_remediation([f1, f2], [artefact_for_f2])

    assert f1.remediation == FALLBACK_REMEDIATION_TEXT
    assert f2.remediation == "Only for f2."
