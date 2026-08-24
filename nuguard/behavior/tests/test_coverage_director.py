"""Unit tests for nuguard/behavior/coverage_director.py."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from nuguard.behavior.coverage_director import CoverageDirector
from nuguard.behavior.models import IntentProfile
from nuguard.common.discovery import DiscoveredProfile


def _make_intent() -> IntentProfile:
    return IntentProfile(
        app_purpose="Banking assistant",
        core_capabilities=["check balance", "transfer funds"],
    )


async def test_next_message_returns_none_when_nothing_uncovered():
    director = CoverageDirector(llm_client=None, intent=_make_intent())
    result = await director.next_message(
        uncovered=set(),
        last_response="Here is your balance.",
        component_descriptions={},
    )
    assert result is None


async def test_next_message_uses_template_when_no_llm_client():
    director = CoverageDirector(llm_client=None, intent=_make_intent())
    result = await director.next_message(
        uncovered={"transfer_funds"},
        last_response="Here is your balance.",
        component_descriptions={"transfer_funds": "move money between accounts"},
    )
    assert result is not None
    assert "transfer_funds" in result


async def test_next_message_uses_llm_response():
    llm = AsyncMock()
    llm.api_key = "fake-key"
    llm.complete = AsyncMock(
        return_value=json.dumps({"target": "transfer_funds", "message": "Can you move $50 to savings?"})
    )
    director = CoverageDirector(llm_client=llm, intent=_make_intent())
    result = await director.next_message(
        uncovered={"transfer_funds", "get_balance"},
        last_response="Your balance is $500.",
        component_descriptions={"transfer_funds": "move money between accounts"},
        allowed_topics=["Fund transfers"],
        profile=DiscoveredProfile(customer_name="Alice", ids=["ACCT-001"]),
    )
    assert result == "Can you move $50 to savings?"
    llm.complete.assert_awaited_once()


async def test_next_message_falls_back_to_template_on_llm_error():
    llm = AsyncMock()
    llm.api_key = "fake-key"
    llm.complete = AsyncMock(side_effect=RuntimeError("boom"))
    director = CoverageDirector(llm_client=llm, intent=_make_intent())
    result = await director.next_message(
        uncovered={"transfer_funds"},
        last_response="Your balance is $500.",
        component_descriptions={"transfer_funds": "move money between accounts"},
    )
    assert result is not None
    assert "transfer_funds" in result


async def test_next_message_falls_back_to_template_on_unparseable_response():
    llm = AsyncMock()
    llm.api_key = "fake-key"
    llm.complete = AsyncMock(return_value="not json at all")
    director = CoverageDirector(llm_client=llm, intent=_make_intent())
    result = await director.next_message(
        uncovered={"transfer_funds"},
        last_response="Your balance is $500.",
        component_descriptions={"transfer_funds": "move money between accounts"},
    )
    assert result is not None
    assert "transfer_funds" in result
