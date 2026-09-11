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


def test_backfill_uses_per_finding_rationale_when_artefact_was_merged():
    # A merged artefact's own `rationale` is a joined blob covering several
    # findings; each finding must get back its OWN text, not a sibling's,
    # even though both finding_ids point at the same artefact object.
    f1 = _finding("f1")
    f2 = _finding("f2")
    merged = _artefact(
        ["f1", "f2"],
        rationale="f1's own text.\nf2's own text.",
    )
    merged.per_finding_rationale = {
        "f1": "f1's own text.",
        "f2": "f2's own text.",
    }

    backfill_finding_remediation([f1, f2], [merged])

    assert f1.remediation == "f1's own text."
    assert f2.remediation == "f2's own text."


def test_backfill_truncation_does_not_cross_contaminate_merged_findings():
    # Regression test: before per_finding_rationale, truncating the merged
    # blob at max_len could leave only f1's text visible while f2's own
    # remediation field silently became a copy of f1's — even though the two
    # findings are unrelated. Confirm each finding's remediation is bounded
    # to its own content only.
    f1 = _finding("f1")
    f2 = _finding("f2")
    merged = _artefact(["f1", "f2"], rationale="f1 rationale text.\nf2 rationale text.")
    merged.per_finding_rationale = {
        "f1": "f1 rationale text.",
        "f2": "f2 rationale text.",
    }

    backfill_finding_remediation([f1, f2], [merged], max_len=15)

    assert "f2" not in f1.remediation
    assert "f1" not in f2.remediation


def test_backfill_falls_back_to_shared_rationale_when_not_merged():
    finding = _finding("f1")
    artefact = _artefact(["f1"], rationale="Unmerged rationale.")

    backfill_finding_remediation([finding], [artefact])

    assert finding.remediation == "Unmerged rationale."
