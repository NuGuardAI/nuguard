"""Tests for LLMClient.complete_stream()'s transient-error retry logic.

Covers the fix for litellm.MidStreamFallbackError (e.g. an Azure socket
timeout partway through generation) — a ServiceUnavailableError subclass
that fires *after* real content has already been streamed to the caller.
Blindly retrying by re-issuing the whole request would re-yield the
completion from scratch, so an accumulator-style caller (``result += chunk``,
as used by RemediationSynthesizer._call_llm_async) would end up with the
partial first attempt's text followed by the full second attempt's text
concatenated together — silently corrupted output instead of a clean retry.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import litellm
import pytest
from litellm.exceptions import MidStreamFallbackError

from nuguard.common.llm_client import LLMClient


def _chunk(content: str | None) -> SimpleNamespace:
    delta = SimpleNamespace(content=content)
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice], usage=None)


class _FakeStream:
    """Async-iterable that yields the given chunks, then optionally raises."""

    def __init__(self, chunks: list, error: Exception | None = None) -> None:
        self._chunks = chunks
        self._error = error

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            yield c
        if self._error is not None:
            raise self._error


def _client() -> LLMClient:
    return LLMClient(model="azure/test-model", api_key="fake-key")


@pytest.mark.asyncio
async def test_mid_stream_error_after_partial_output_does_not_retry_or_duplicate():
    """A transient error raised after real content was already yielded must
    stop the generator rather than restart the whole request — restarting
    would duplicate the already-yielded text for accumulator-style callers."""
    client = _client()
    err = MidStreamFallbackError(
        message="Timeout on reading data from socket",
        model=client.model,
        llm_provider="azure",
    )
    stream = _FakeStream([_chunk("Hello "), _chunk("wor")], error=err)

    with patch("litellm.acompletion", new=AsyncMock(return_value=stream)) as mocked:
        result = ""
        async for piece in client.complete_stream("prompt", label="test"):
            result += piece

    assert result == "Hello wor"
    # Exactly one request was made — no retry/restart after partial content.
    assert mocked.call_count == 1


@pytest.mark.asyncio
async def test_transient_error_before_any_output_retries_and_succeeds():
    """A transient error with zero content yielded yet (the common case —
    e.g. a connection refused before the first chunk) should retry normally
    and succeed via the second attempt."""
    client = _client()
    err = litellm.APIConnectionError(
        message="connection refused", model=client.model, llm_provider="azure",
    )
    failing_stream = _FakeStream([], error=err)
    success_stream = _FakeStream([_chunk("all good")])

    with patch(
        "litellm.acompletion",
        new=AsyncMock(side_effect=[failing_stream, success_stream]),
    ) as mocked, patch("asyncio.sleep", new=AsyncMock()):
        result = ""
        async for piece in client.complete_stream("prompt", label="test"):
            result += piece

    assert result == "all good"
    assert mocked.call_count == 2
