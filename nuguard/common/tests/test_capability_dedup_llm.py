"""Tests for llm_dedup_capability_names (Phase 4: LLM-assisted semantic
dedup for capability/tool discovery, additive on top of the existing
str.lower() heuristic in apply_capability_discovery)."""
from __future__ import annotations

import json

import pytest

from nuguard.common.capability_dedup_llm import llm_dedup_capability_names


class _FakeLLM:
    def __init__(self, response: str | None = None, api_key: str | None = "fake-key", raise_exc: Exception | None = None):
        self.api_key = api_key
        self._response = response
        self._raise_exc = raise_exc
        self.calls: list[dict] = []

    async def complete(self, prompt: str, system: str | None = None, label: str = "", **kwargs):
        self.calls.append({"prompt": prompt, "system": system, "label": label})
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response or "{}"


@pytest.mark.asyncio
async def test_near_duplicate_names_deduped_via_llm():
    llm = _FakeLLM(response=json.dumps({"SendEmailTool": "send_email"}))
    result = await llm_dedup_capability_names(["SendEmailTool"], ["send_email"], llm)
    assert result == {"SendEmailTool": "send_email"}
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_heuristic_result_unchanged():
    llm = _FakeLLM(raise_exc=RuntimeError("boom"))
    result = await llm_dedup_capability_names(["SendEmailTool"], ["send_email"], llm)
    assert result == {}


@pytest.mark.asyncio
async def test_disabled_by_default_no_llm_call_made():
    llm = _FakeLLM(response=json.dumps({"SendEmailTool": "send_email"}))
    result = await llm_dedup_capability_names(["SendEmailTool"], ["send_email"], None)
    assert result == {}
    assert llm.calls == []


@pytest.mark.asyncio
async def test_canned_llm_client_response_does_not_cause_false_dedup():
    # api_key=None mirrors LLMClient's real "not configured" state.
    llm = _FakeLLM(response=json.dumps({"SendEmailTool": "send_email"}), api_key=None)
    result = await llm_dedup_capability_names(["SendEmailTool"], ["send_email"], llm)
    assert result == {}
    assert llm.calls == []  # short-circuited before ever calling the LLM

    # Belt-and-suspenders: even if a canned-response marker slips through
    # some other path, it must never be trusted as a real mapping.
    canned_llm = _FakeLLM(
        response="[NUGUARD_CANNED_RESPONSE] Template analysis for: '...'",
        api_key="fake-key",
    )
    result2 = await llm_dedup_capability_names(["SendEmailTool"], ["send_email"], canned_llm)
    assert result2 == {}


@pytest.mark.asyncio
async def test_hallucinated_mapping_entries_are_dropped():
    llm = _FakeLLM(
        response=json.dumps(
            {
                "SendEmailTool": "send_email",  # valid
                "NotACandidate": "send_email",  # key not in candidates
                "SendEmailTool2": "not_existing",  # value not in existing
            }
        )
    )
    result = await llm_dedup_capability_names(["SendEmailTool", "SendEmailTool2"], ["send_email"], llm)
    assert result == {"SendEmailTool": "send_email"}


@pytest.mark.asyncio
async def test_empty_candidates_or_existing_short_circuits():
    llm = _FakeLLM(response=json.dumps({"a": "b"}))
    assert await llm_dedup_capability_names([], ["send_email"], llm) == {}
    assert await llm_dedup_capability_names(["SendEmailTool"], [], llm) == {}
    assert llm.calls == []


@pytest.mark.asyncio
async def test_malformed_json_response_returns_empty():
    llm = _FakeLLM(response="not json at all")
    result = await llm_dedup_capability_names(["SendEmailTool"], ["send_email"], llm)
    assert result == {}


class TestApplyCapabilityDiscoveryLLMIntegration:
    """apply_capability_discovery() with llm=None (the default) must behave
    identically to before Phase 4 — this is a regression guard, not new
    coverage of the merge logic itself."""

    @pytest.mark.asyncio
    async def test_llm_none_default_matches_pre_phase4_heuristic_behavior(self):
        from nuguard.common.discovery import (
            AgentCapabilityGap,
            CapabilityDiscoveryResult,
            apply_capability_discovery,
        )
        from nuguard.sbom.models import AiSbomDocument, Node
        from nuguard.sbom.types import ComponentType

        agent = Node(name="Support Agent", component_type=ComponentType.AGENT, confidence=1.0)
        existing_tool = Node(name="send_email", component_type=ComponentType.TOOL, confidence=1.0)
        sbom = AiSbomDocument(target="./app", nodes=[agent, existing_tool])
        gap = AgentCapabilityGap(agent_id=str(agent.id), agent_name=agent.name, needs_tools=True)
        result = CapabilityDiscoveryResult(
            raw_responses={"tools": "- SendEmailTool\n- LookupOrderTool"}
        )

        notes = await apply_capability_discovery(sbom, [gap], result, llm=None)

        tool_names = {n.name for n in sbom.nodes if n.component_type == ComponentType.TOOL}
        # No LLM configured -> no semantic dedup -> both survive as distinct
        # heuristic-new names, exactly as before Phase 4 existed.
        assert tool_names == {"send_email", "SendEmailTool", "LookupOrderTool"}
        assert any("SendEmailTool" in n for n in notes)

    @pytest.mark.asyncio
    async def test_llm_dedup_collapses_near_duplicate_tool_name(self):
        from nuguard.common.discovery import (
            AgentCapabilityGap,
            CapabilityDiscoveryResult,
            apply_capability_discovery,
        )
        from nuguard.sbom.models import AiSbomDocument, Node
        from nuguard.sbom.types import ComponentType

        agent = Node(name="Support Agent", component_type=ComponentType.AGENT, confidence=1.0)
        existing_tool = Node(name="send_email", component_type=ComponentType.TOOL, confidence=1.0)
        sbom = AiSbomDocument(target="./app", nodes=[agent, existing_tool])
        gap = AgentCapabilityGap(agent_id=str(agent.id), agent_name=agent.name, needs_tools=True)
        result = CapabilityDiscoveryResult(raw_responses={"tools": "- SendEmailTool"})
        llm = _FakeLLM(response=json.dumps({"SendEmailTool": "send_email"}))

        notes = await apply_capability_discovery(sbom, [gap], result, llm=llm)

        tool_names = {n.name for n in sbom.nodes if n.component_type == ComponentType.TOOL}
        assert tool_names == {"send_email"}  # SendEmailTool was not added as a new node
        assert any("LLM dedup" in n for n in notes)
