"""Regression tests for issue #246.

``LLMClient.complete`` historically attributed a call's token usage with a
before/after ``sum(client.token_counts)`` delta of the *shared cumulative*
counters. Under concurrent verification (``config.llm_concurrency > 1``)
another in-flight call can increment the shared counter between the two
reads, so one call's "own usage" could absorb tokens spent by a sibling —
inflating ``verification_cost`` / ``total_cost`` stats. The fix threads a
per-call accumulator through ``complete()`` and exposes it as
``call_token_counts`` so concurrent calls each read exactly their own usage.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from nuguard.common import llm_client as _llm_client_module
from nuguard.common.llm_client import LLMClient


class _Usage:
    """Mutable, shared usage object — one instance is shared by all calls."""

    def __init__(self) -> None:
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0

    def add(self, p: int, c: int) -> None:
        self.prompt_tokens += p
        self.completion_tokens += c


def _make_stream_that_records_usage(client: LLMClient, usage: _Usage, seen_per_call: list[int]):
    """Patch ``client.complete_stream`` to mimic real usage recording.

    Yields strings like the real stream, bumps the shared cumulative counters,
    and — crucially — also bumps the per-call accumulator if armed, exactly
    like the production code path. The test then asserts each concurrent call
    observed ONLY its own usage via ``client.call_token_counts`` (read inside
    the stream, which lives inside the ``complete()`` context).
    """

    async def _gen() -> AsyncIterator[str]:
        for _ in range(1):
            usage.add(100, 50)
            client._input_tokens += 100
            client._output_tokens += 50
            if _llm_client_module._CALL_COUNTER_READY.get():
                slots = _llm_client_module._call_token_slots.get()
                if slots is not None:
                    slots[0] += 100
                    slots[1] += 50
            seen_per_call.append(sum(client.call_token_counts))
            yield "ok"

    return _gen()


@pytest.mark.parametrize("concurrency", [1, 4])
async def test_call_token_counts_attributes_only_own_usage(
    concurrency: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: per-call token read must never absorb siblings' usage."""
    client = LLMClient(model="gemini/another provider-3.1-flash-lite", api_key="test")
    usage = _Usage()
    seen: list[int] = []

    async def fake_complete_stream(prompt: str, system: str | None = None, label: str = "", **kwargs: object):
        gen = _make_stream_that_records_usage(client, usage, seen)
        async for chunk in gen:
            yield chunk

    client.complete_stream = fake_complete_stream  # type: ignore[method-assign]
    try:
        await asyncio.gather(*(client.complete("prompt") for _ in range(concurrency)))
    finally:
        # Restore so the global client isn't left patched.
        client.complete_stream = LLMClient.complete_stream  # type: ignore[method-assign]

    # Every concurrent call must observe exactly its own usage (150).
    for own in seen:
        assert own == 150, f"call attributed {own}, expected 150"
    # The shared cumulative counter still records the TOTAL across all calls.
    assert sum(client.token_counts) == concurrency * 150