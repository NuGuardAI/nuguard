"""Tests for RemediationSynthesizer's LLM-authored "surgical fix" prompts:
which handlers get LLM-authored patch_text vs. rationale, and that an
unavailable LLM client (not configured at all) falls back to template text
while an actual LLM failure (canned response, exception) propagates loudly
instead of being silently swallowed."""
from __future__ import annotations

import pytest

from nuguard.remediation.models import RemediationArtefactType
from nuguard.remediation.prompts import (
    GUARDRAIL_RATIONALE_SYSTEM,
    SYSTEM_PROMPT_PATCH_SYSTEM,
)
from nuguard.remediation.synthesizer import RemediationSynthesizer


class _FakeLLM:
    """Minimal LLMClient stand-in recording every complete_stream() call.

    Carries a non-empty ``api_key`` so it's treated as an explicitly
    configured client — the synthesizer only silently no-ops for a client
    with no key at all; once a key is set, any failure must propagate.
    """

    def __init__(self, response: str = "surgical fix text") -> None:
        self._response = response
        self.calls: list[dict] = []
        self.api_key = "fake-key"

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
async def test_canned_response_raises_instead_of_silently_falling_back():
    # A canned response means the configured LLM client is broken (no API
    # key, bad credentials, etc.) — that must surface as a loud failure, not
    # silently downgrade every remediation to generic template text.
    llm = _FakeLLM(response="[NUGUARD_CANNED_RESPONSE] Template analysis for: x")
    synth = RemediationSynthesizer(llm_client=llm)

    with pytest.raises(RuntimeError, match="canned fallback response"):
        await synth.synthesize_findings_async([_blocked_topics_finding()])


@pytest.mark.asyncio
async def test_llm_exception_propagates_instead_of_silently_falling_back():
    class _RaisingLLM:
        api_key = "fake-key"

        async def complete_stream(self, prompt, system=None, label=""):
            raise RuntimeError("provider down")
            yield  # pragma: no cover - make this an async generator

    synth = RemediationSynthesizer(llm_client=_RaisingLLM())

    with pytest.raises(RuntimeError, match="provider down"):
        await synth.synthesize_findings_async([_blocked_topics_finding()])


@pytest.mark.asyncio
async def test_no_llm_client_produces_template_only():
    synth = RemediationSynthesizer(llm_client=None)

    artefacts = await synth.synthesize_findings_async([_blocked_topics_finding()])

    patch = next(a for a in artefacts if a.artefact_type == RemediationArtefactType.SYSTEM_PROMPT_PATCH)
    assert "Out of Scope" in patch.patch_text


class TestEvidencePriorityInLLMEnrichment:
    """_enrich_artefacts_async() builds its `evidence` string as
    evidence_quote -> reasoning -> description -> title (synthesizer.py, the
    scenario_type-routing fix's Step 3) so the LLM prompt is grounded in the
    specific proof of breach rather than generic telemetry text like "Attack
    scenario 'X' succeeded: success signals detected in N step(s)."."""

    @pytest.mark.asyncio
    async def test_evidence_quote_used_over_generic_description_system_prompt_patch(self) -> None:
        llm = _FakeLLM(response="patched")
        synth = RemediationSynthesizer(llm_client=llm)
        finding = {
            **_blocked_topics_finding(),
            "description": "Attack scenario 'X' succeeded: success signals detected in 1 step(s).",
            "evidence_quote": "SPECIFIC_PROOF_STRING_12345",
        }

        await synth.synthesize_findings_async([finding])

        prompts = [c["prompt"] for c in llm.calls if c["system"] == SYSTEM_PROMPT_PATCH_SYSTEM]
        assert prompts, "expected a SYSTEM_PROMPT_PATCH_SYSTEM call"
        assert any("SPECIFIC_PROOF_STRING_12345" in p for p in prompts)
        assert not any("success signals detected" in p for p in prompts)

    @pytest.mark.asyncio
    async def test_reasoning_used_over_generic_description_guardrail_rationale(self) -> None:
        llm = _FakeLLM(response="rationale text")
        synth = RemediationSynthesizer(llm_client=llm)
        finding = {
            **_data_leak_finding(),
            "description": "Attack scenario 'Y' succeeded: success signals detected in 1 step(s).",
            "reasoning": "SPECIFIC_REASONING_STRING_67890",
        }

        await synth.synthesize_findings_async([finding])

        prompts = [c["prompt"] for c in llm.calls if c["system"] == GUARDRAIL_RATIONALE_SYSTEM]
        assert prompts, "expected a GUARDRAIL_RATIONALE_SYSTEM call"
        assert any("SPECIFIC_REASONING_STRING_67890" in p for p in prompts)
        assert not any("success signals detected" in p for p in prompts)

    @pytest.mark.asyncio
    async def test_evidence_quote_takes_priority_over_reasoning(self) -> None:
        llm = _FakeLLM(response="rationale text")
        synth = RemediationSynthesizer(llm_client=llm)
        finding = {
            **_data_leak_finding(),
            "evidence_quote": "EVIDENCE_QUOTE_WINS",
            "reasoning": "REASONING_SHOULD_NOT_APPEAR",
        }

        await synth.synthesize_findings_async([finding])

        prompts = [c["prompt"] for c in llm.calls if c["system"] == GUARDRAIL_RATIONALE_SYSTEM]
        assert any("EVIDENCE_QUOTE_WINS" in p for p in prompts)
        assert not any("REASONING_SHOULD_NOT_APPEAR" in p for p in prompts)

    @pytest.mark.asyncio
    async def test_falls_back_to_description_when_no_evidence_quote_or_reasoning(self) -> None:
        # Regression guard: findings without the new fields (pre-existing
        # data, or behavior/analysis findings which never populate them)
        # must keep working exactly as before this change.
        llm = _FakeLLM(response="patched")
        synth = RemediationSynthesizer(llm_client=llm)
        finding = _blocked_topics_finding()  # no evidence_quote/reasoning keys at all

        await synth.synthesize_findings_async([finding])

        prompts = [c["prompt"] for c in llm.calls if c["system"] == SYSTEM_PROMPT_PATCH_SYSTEM]
        assert any(finding["description"] in p for p in prompts)
