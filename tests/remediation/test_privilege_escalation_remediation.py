"""Regression tests for two remediation-synthesis bugs found validating
kscope's remediation plan against its SBOM and redteam findings:

1. ``_remediate_privilege_escalation_async`` fell back to a literal
   ``"high-privilege-tool"`` placeholder when no real tool name could be
   resolved, and wrote it into the plan as if it were a real SBOM component
   (e.g. an auth-bypass finding with no associated tool). Its sync twin,
   ``_remediate_privilege_escalation``, already guarded this case with a
   generic architectural-review artefact — the two paths had drifted apart.
2. ``_add_output_redactor``'s fallback field list (used when the SBOM has no
   classified PII/PHI fields for the affected node) was banking-specific
   (account_number, routing_number, ...), producing a remediation rationale
   about "financial routing numbers" for findings whose real evidence was
   about something else entirely (e.g. an email address).
"""
from __future__ import annotations

import pytest

from nuguard.remediation.models import RemediationArtefactType
from nuguard.remediation.synthesizer import RemediationSynthesizer


def _auth_bypass_finding() -> dict:
    return {
        "finding_id": "authentication-bypass-any-auth-login",
        "title": "Authentication Bypass — ANY /auth/login",
        "description": "Attack scenario 'Authentication Bypass — ANY /auth/login' succeeded.",
        "affected_component": "ANY /auth/login",
        "severity": "high",
        "goal_type": "api_attack",
    }


def _privilege_finding_with_tool_in_title(tool_name: str) -> dict:
    return {
        "finding_id": "f1",
        "title": f"Unauthenticated agent 'SupportAgent' can access high-privilege tool '{tool_name}'",
        "description": "Agent invoked a high-privilege tool without authentication.",
        "affected_component": "SupportAgent",
        "severity": "critical",
        "goal_type": "privilege_escalation",
    }


def _aggregated_pii_finding() -> dict:
    return {
        "finding_id": "topic-boundary-aggregated-pii-across-turns",
        "title": "Topic Boundary — Aggregated PII Across Turns — Healthcare Service Assistant",
        "description": "Response has no overlap with any allowed_topics keyword.",
        "affected_component": "Healthcare Service Assistant",
        "severity": "low",
        "goal_type": "data_exfiltration",
        "scenario_type": "AGGREGATED_PII",
    }


class TestPrivilegeEscalationAsyncNoToolName:
    @pytest.mark.asyncio
    async def test_no_tool_name_returns_generic_review_not_placeholder(self) -> None:
        synth = RemediationSynthesizer()

        artefacts = await synth.synthesize_findings_async([_auth_bypass_finding()])

        assert len(artefacts) == 1
        artefact = artefacts[0]
        assert artefact.artefact_type == RemediationArtefactType.ARCHITECTURAL_CHANGE
        rendered = " ".join(
            str(v) for v in (artefact.change_description, artefact.change_detail, artefact.rationale) if v
        )
        assert "high-privilege-tool" not in rendered
        assert "ANY /auth/login" in rendered

    @pytest.mark.asyncio
    async def test_resolves_tool_name_from_title(self) -> None:
        synth = RemediationSynthesizer()

        artefacts = await synth.synthesize_findings_async(
            [_privilege_finding_with_tool_in_title("get_ssn")]
        )

        rendered = " ".join(
            str(getattr(a, f, "")) for a in artefacts
            for f in ("change_description", "change_detail", "patch_text", "rationale", "guardrail_trigger")
        )
        assert "get_ssn" in rendered
        assert "high-privilege-tool" not in rendered


class TestSyncAsyncParity:
    def test_sync_and_async_agree_on_missing_tool_name(self) -> None:
        synth = RemediationSynthesizer()
        finding = _auth_bypass_finding()

        sync_artefacts = synth._remediate_privilege_escalation(
            "ANY /auth/login", None, finding, finding["finding_id"], "high"
        )

        import asyncio
        async_artefacts = asyncio.run(
            synth._remediate_privilege_escalation_async(
                "ANY /auth/login", None, finding, finding["finding_id"], "high"
            )
        )

        assert len(sync_artefacts) == len(async_artefacts) == 1
        assert sync_artefacts[0].artefact_type == async_artefacts[0].artefact_type
        assert sync_artefacts[0].change_description == async_artefacts[0].change_description
        assert sync_artefacts[0].change_detail == async_artefacts[0].change_detail


class TestOutputRedactorFallbackFields:
    @pytest.mark.asyncio
    async def test_fallback_fields_are_domain_neutral(self) -> None:
        synth = RemediationSynthesizer()

        artefacts = await synth.synthesize_findings_async([_aggregated_pii_finding()])

        guardrail = next(a for a in artefacts if a.artefact_type == RemediationArtefactType.OUTPUT_GUARDRAIL)
        assert "email" in guardrail.guardrail_trigger
        assert "name" in guardrail.guardrail_trigger
        # Financial/credential fields remain present too — this is additive, not a replacement.
        assert "ssn" in guardrail.guardrail_trigger
