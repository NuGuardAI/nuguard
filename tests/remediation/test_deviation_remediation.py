"""Tests for enrich_deviation_remediations_async: LLM-authored, transcript-
grounded remediation text for per-turn behavior deviations (which never go
through RemediationSynthesizer/backfill since they aren't Finding objects)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nuguard.remediation.deviation import enrich_deviation_remediations_async
from nuguard.remediation.prompts import DEVIATION_REMEDIATION_SYSTEM


class _FakeLLM:
    """Minimal LLMClient stand-in recording every complete_stream() call."""

    def __init__(self, response: str = "surgical fix text") -> None:
        self._response = response
        self.calls: list[dict] = []

    async def complete_stream(self, prompt, system=None, label=""):
        self.calls.append({"prompt": prompt, "system": system, "label": label})
        yield self._response


def _scenario_result(verdicts: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(verdicts=verdicts)


def _wheelchair_verdict() -> dict:
    return {
        "user_message": "Can you retrieve the FAQ answer on how to request wheelchair assistance?",
        "agent_response": "I'm sorry, I don't know the answer to that question.",
        "gaps": ["No FAQ answer was retrieved or provided for the wheelchair assistance question."],
        "deviations": [
            {
                "deviation_type": "capability_gap",
                "description": "No FAQ answer was retrieved or provided for the wheelchair assistance question.",
                "severity": "medium",
            }
        ],
    }


@pytest.mark.asyncio
async def test_enriches_deviation_with_llm_grounded_text():
    llm = _FakeLLM(response="Add a wheelchair-assistance entry to the FAQ knowledge base; the agent has no content to answer from.")
    scenario_results = [_scenario_result([_wheelchair_verdict()])]

    await enrich_deviation_remediations_async(scenario_results, llm)

    dev = scenario_results[0].verdicts[0]["deviations"][0]
    assert dev["remediation"] == (
        "Add a wheelchair-assistance entry to the FAQ knowledge base; "
        "the agent has no content to answer from."
    )
    assert len(llm.calls) == 1
    assert llm.calls[0]["system"] == DEVIATION_REMEDIATION_SYSTEM
    assert "wheelchair" in llm.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_no_llm_client_leaves_remediation_unset():
    scenario_results = [_scenario_result([_wheelchair_verdict()])]

    await enrich_deviation_remediations_async(scenario_results, None)

    dev = scenario_results[0].verdicts[0]["deviations"][0]
    assert "remediation" not in dev


@pytest.mark.asyncio
async def test_canned_response_leaves_remediation_unset():
    llm = _FakeLLM(response="[NUGUARD_CANNED_RESPONSE] Template analysis for: x")
    scenario_results = [_scenario_result([_wheelchair_verdict()])]

    await enrich_deviation_remediations_async(scenario_results, llm)

    dev = scenario_results[0].verdicts[0]["deviations"][0]
    assert "remediation" not in dev


@pytest.mark.asyncio
async def test_dedupes_identical_deviation_across_turns():
    verdict_a = _wheelchair_verdict()
    verdict_b = _wheelchair_verdict()  # same (deviation_type, description)
    llm = _FakeLLM(response="Add the wheelchair-assistance FAQ entry.")
    scenario_results = [_scenario_result([verdict_a, verdict_b])]

    await enrich_deviation_remediations_async(scenario_results, llm)

    assert len(llm.calls) == 1
    assert verdict_a["deviations"][0]["remediation"] == "Add the wheelchair-assistance FAQ entry."
    assert verdict_b["deviations"][0]["remediation"] == "Add the wheelchair-assistance FAQ entry."


@pytest.mark.asyncio
async def test_no_deviations_is_a_noop():
    scenario_results = [_scenario_result([{"deviations": []}])]
    llm = _FakeLLM()

    await enrich_deviation_remediations_async(scenario_results, llm)

    assert llm.calls == []
