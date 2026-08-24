"""Tests for nuguard/behavior/refusal.py — refusal detection and classification."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from nuguard.behavior.refusal import RefusalReason, classify_refusal

pytestmark = pytest.mark.asyncio


async def test_classify_refusal_missing_precondition():
    text = "I'd be happy to help — could you provide the account ID before I can proceed?"
    assert await classify_refusal(text) == RefusalReason.MISSING_PRECONDITION


async def test_classify_refusal_permission_denied():
    text = "You don't have permission to access this resource — it requires manager approval."
    assert await classify_refusal(text) == RefusalReason.PERMISSION_DENIED


async def test_classify_refusal_nl_routing_miss():
    text = "I'm not sure what you mean — could you clarify your request?"
    assert await classify_refusal(text) == RefusalReason.NL_ROUTING_MISS


async def test_classify_refusal_out_of_scope_deflection():
    text = "I'm sorry, that's outside my scope and not something I'm able to help with."
    assert await classify_refusal(text) == RefusalReason.OUT_OF_SCOPE_DEFLECTION


async def test_classify_refusal_server_error():
    text = "Sorry, something went wrong on our end. Please try again later."
    assert await classify_refusal(text) == RefusalReason.SERVER_ERROR


async def test_classify_refusal_genuine_engagement_returns_none():
    text = (
        "Sure! Your current balance is $1,234.56 as of this morning. "
        "Would you like a breakdown by account?"
    )
    assert await classify_refusal(text) is None


async def test_classify_refusal_empty_response_without_llm_treated_as_routing_miss():
    assert await classify_refusal("") == RefusalReason.NL_ROUTING_MISS


async def test_classify_refusal_llm_fallback_used_when_ambiguous():
    """A response with no heuristic hit at all is ambiguous only when it's
    empty/whitespace — anything else with real content and no refusal tokens
    is treated as genuine engagement without needing an LLM call. This test
    exercises the LLM fallback path directly via a response the heuristics
    cannot classify with confidence (empty), confirming the LLM's answer wins
    over the heuristic default when a client is supplied.
    """
    llm = AsyncMock()
    llm.api_key = "fake-key"
    llm.complete = AsyncMock(return_value=json.dumps({"label": "permission_denied"}))

    result = await classify_refusal("", llm_client=llm)
    assert result == RefusalReason.PERMISSION_DENIED
    llm.complete.assert_awaited_once()


async def test_classify_refusal_llm_fallback_none_label():
    llm = AsyncMock()
    llm.api_key = "fake-key"
    llm.complete = AsyncMock(return_value=json.dumps({"label": "none"}))

    result = await classify_refusal("", llm_client=llm)
    assert result is None


async def test_classify_refusal_llm_error_falls_back_to_none():
    llm = AsyncMock()
    llm.api_key = "fake-key"
    llm.complete = AsyncMock(side_effect=RuntimeError("boom"))

    result = await classify_refusal("", llm_client=llm)
    assert result is None


async def test_classify_refusal_no_llm_client_skips_call_for_ambiguous_case():
    # No llm_client and no api_key on a dummy client — heuristic-only path.
    dummy = AsyncMock()
    dummy.api_key = None
    result = await classify_refusal("", llm_client=dummy)
    assert result == RefusalReason.NL_ROUTING_MISS
    dummy.complete.assert_not_called()
