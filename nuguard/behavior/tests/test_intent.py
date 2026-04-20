"""Unit tests for nuguard/behavior/intent.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nuguard.behavior.intent import _deterministic_intent, extract_intent
from nuguard.behavior.models import IntentProfile
from nuguard.models.policy import CognitivePolicy


def _make_policy(
    allowed_topics=None,
    restricted_topics=None,
    restricted_actions=None,
    hitl_triggers=None,
    data_classification=None,
    rate_limits=None,
) -> CognitivePolicy:
    return CognitivePolicy(
        allowed_topics=allowed_topics or [],
        restricted_topics=restricted_topics or [],
        restricted_actions=restricted_actions or [],
        hitl_triggers=hitl_triggers or [],
        data_classification=data_classification or [],
        rate_limits=rate_limits or {},
    )


# ---------------------------------------------------------------------------
# Deterministic extraction
# ---------------------------------------------------------------------------


def test_deterministic_minimal_policy():
    policy = _make_policy(allowed_topics=["marketing", "advertising"])
    intent = _deterministic_intent(policy)
    assert "marketing" in intent.app_purpose or len(intent.core_capabilities) > 0


def test_deterministic_full_policy():
    policy = _make_policy(
        allowed_topics=["ad copy generation", "competitor research"],
        restricted_topics=["financial advice", "medical advice"],
        restricted_actions=["send email without approval"],
        hitl_triggers=["budget > $10k"],
        data_classification=["no PII in logs"],
    )
    intent = _deterministic_intent(policy)
    assert len(intent.core_capabilities) >= 2
    assert any("financial advice" in b for b in intent.behavioral_bounds)
    assert any("send email" in b for b in intent.behavioral_bounds)
    assert "no PII in logs" in intent.data_handling_rules
    assert "budget > $10k" in intent.escalation_rules


def test_deterministic_with_sbom():
    from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata, NodeType, ScanSummary
    nodes = [
        Node(name="CopyAgent", component_type=NodeType.AGENT, confidence=1.0, metadata=NodeMetadata()),
        Node(name="search_tool", component_type=NodeType.TOOL, confidence=1.0, metadata=NodeMetadata()),
    ]
    sbom = AiSbomDocument(
        target="test",
        nodes=nodes,
        summary=ScanSummary(use_case="marketing campaign AI"),
    )
    policy = _make_policy()
    intent = _deterministic_intent(policy, sbom)
    assert "marketing campaign AI" in intent.app_purpose


def test_deterministic_empty_policy():
    policy = _make_policy()
    intent = _deterministic_intent(policy)
    assert intent.app_purpose == "AI application"
    assert intent.behavioral_bounds == []
    assert intent.escalation_rules == []


# ---------------------------------------------------------------------------
# async extract_intent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_intent_no_llm():
    policy = _make_policy(allowed_topics=["coding assistance"])
    intent = await extract_intent(policy, llm_client=None)
    assert isinstance(intent, IntentProfile)
    assert len(intent.core_capabilities) > 0


@pytest.mark.asyncio
async def test_extract_intent_llm_success():
    policy = _make_policy(allowed_topics=["coding assistance"])
    mock_llm = MagicMock()
    mock_llm.api_key = "test-key"
    mock_llm.complete = AsyncMock(return_value="""{
        "app_purpose": "AI coding assistant",
        "core_capabilities": ["write code", "debug code"],
        "behavioral_bounds": [],
        "data_handling_rules": [],
        "escalation_rules": []
    }""")

    intent = await extract_intent(policy, llm_client=mock_llm)
    assert intent.app_purpose == "AI coding assistant"
    assert "write code" in intent.core_capabilities


@pytest.mark.asyncio
async def test_extract_intent_llm_returns_garbage():
    policy = _make_policy(allowed_topics=["sales"])
    mock_llm = MagicMock()
    mock_llm.api_key = "test-key"
    mock_llm.complete = AsyncMock(return_value="this is not json at all")

    intent = await extract_intent(policy, llm_client=mock_llm)
    # Should fall back to deterministic
    assert isinstance(intent, IntentProfile)
    assert len(intent.core_capabilities) > 0


@pytest.mark.asyncio
async def test_extract_intent_llm_raises():
    policy = _make_policy(allowed_topics=["sales"])
    mock_llm = MagicMock()
    mock_llm.api_key = "test-key"
    mock_llm.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

    intent = await extract_intent(policy, llm_client=mock_llm)
    # Should fall back to deterministic
    assert isinstance(intent, IntentProfile)
