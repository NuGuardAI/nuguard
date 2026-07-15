"""Tests for RemediationSynthesizer's LLM-authored "surgical fix" prompts:
which handlers get LLM-authored patch_text vs. rationale, the shared
canned-response guard, and graceful fallback to template text on failure."""
from __future__ import annotations

import pytest

from nuguard.remediation.models import RemediationArtefactType
from nuguard.remediation.prompts import (
    GUARDRAIL_RATIONALE_SYSTEM,
    SYSTEM_PROMPT_PATCH_SYSTEM,
)
from nuguard.remediation.synthesizer import RemediationSynthesizer


class _FakeLLM:
    """Minimal LLMClient stand-in recording every complete_stream() call."""

    def __init__(self, response: str = "surgical fix text") -> None:
        self._response = response
        self.calls: list[dict] = []

    async def complete_stream(self, prompt, system=None, label=""):
        self.calls.append({"prompt": prompt, "system": system, "label": label})
        yield self._response


def _blocked_topics_finding() -> dict:
    return {
        "finding_id": "f1",
        "title": "Restricted topic reachable",
        "description": "Agent discussed competitor pricing when asked.",
        "affected_component": "SupportAgent",
        "severity": "high",
        "goal_type": "prompt_driven_threat",
    }


def _data_leak_finding() -> dict:
    return {
        "finding_id": "f2",
        "title": "PII exfiltrated",
        "description": "Agent leaked account_number in a response.",
        "affected_component": "SupportAgent",
        "severity": "high",
        "goal_type": "data_exfiltration",
    }


@pytest.mark.asyncio
async def test_prompt_patch_dtype_uses_surgical_system_prompt_and_llm_text():
    llm = _FakeLLM(response="Only discuss our own product pricing, never competitors'.")
    synth = RemediationSynthesizer(llm_client=llm)

    artefacts = await synth.synthesize_findings_async([_blocked_topics_finding()])

    patch = next(a for a in artefacts if a.artefact_type == RemediationArtefactType.SYSTEM_PROMPT_PATCH)
    assert patch.patch_text == "Only discuss our own product pricing, never competitors'."
    assert any(c["system"] == SYSTEM_PROMPT_PATCH_SYSTEM for c in llm.calls)


@pytest.mark.asyncio
async def test_guardrail_dtype_upgrades_rationale_but_keeps_spec_deterministic():
    llm = _FakeLLM(response="This redactor stops account_number leaking again.")
    synth = RemediationSynthesizer(llm_client=llm)

    artefacts = await synth.synthesize_findings_async([_data_leak_finding()])

    guardrail = next(a for a in artefacts if a.artefact_type == RemediationArtefactType.OUTPUT_GUARDRAIL)
    assert guardrail.rationale == "This redactor stops account_number leaking again."
    # Structured/actionable fields must stay deterministic — never LLM-authored.
    assert guardrail.guardrail_type == "field_redactor"
    assert guardrail.guardrail_action == "REDACT"
    assert any(c["system"] == GUARDRAIL_RATIONALE_SYSTEM for c in llm.calls)


@pytest.mark.asyncio
async def test_canned_response_falls_back_to_template_text():
    llm = _FakeLLM(response="[NUGUARD_CANNED_RESPONSE] Template analysis for: x")
    synth = RemediationSynthesizer(llm_client=llm)

    artefacts = await synth.synthesize_findings_async([_blocked_topics_finding()])

    patch = next(a for a in artefacts if a.artefact_type == RemediationArtefactType.SYSTEM_PROMPT_PATCH)
    assert "[NUGUARD_CANNED_RESPONSE]" not in patch.patch_text
    assert "Out of Scope" in patch.patch_text  # falls back to the deterministic template


@pytest.mark.asyncio
async def test_llm_exception_falls_back_to_template_text():
    class _RaisingLLM:
        async def complete_stream(self, prompt, system=None, label=""):
            raise RuntimeError("provider down")
            yield  # pragma: no cover - make this an async generator

    synth = RemediationSynthesizer(llm_client=_RaisingLLM())

    artefacts = await synth.synthesize_findings_async([_blocked_topics_finding()])

    patch = next(a for a in artefacts if a.artefact_type == RemediationArtefactType.SYSTEM_PROMPT_PATCH)
    assert "Out of Scope" in patch.patch_text


@pytest.mark.asyncio
async def test_no_llm_client_produces_template_only():
    synth = RemediationSynthesizer(llm_client=None)

    artefacts = await synth.synthesize_findings_async([_blocked_topics_finding()])

    patch = next(a for a in artefacts if a.artefact_type == RemediationArtefactType.SYSTEM_PROMPT_PATCH)
    assert "Out of Scope" in patch.patch_text
