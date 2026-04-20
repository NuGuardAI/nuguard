from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from nuguard.common.llm_client import LLMClient


class _FakeDelta:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.delta = _FakeDelta(content)


class _FakeChunk:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeStream:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        item = _FakeChunk(self._chunks[self._index])
        self._index += 1
        return item


def _install_fake_litellm(captured: dict[str, object]) -> None:
    async def _acompletion(**kwargs):
        captured["kwargs"] = kwargs
        return _FakeStream(["ok"])

    fake_module = SimpleNamespace(
        acompletion=_acompletion,
        AuthenticationError=Exception,
        RateLimitError=Exception,
        ServiceUnavailableError=Exception,
        BadRequestError=Exception,
        APIConnectionError=Exception,
        Timeout=Exception,
    )
    sys.modules["litellm"] = fake_module


def test_complete_stream_drops_none_kwargs() -> None:
    captured: dict[str, object] = {}
    _install_fake_litellm(captured)
    client = LLMClient(model="openai/gpt-4o-mini", api_key="test-key")

    async def _run() -> str:
        return await client.complete("hello", api_base=None, metadata=None)

    result = asyncio.run(_run())
    assert result == "ok"

    forwarded = captured["kwargs"]
    assert isinstance(forwarded, dict)
    assert "api_base" not in forwarded
    assert "metadata" not in forwarded


def test_complete_stream_drops_blank_api_base() -> None:
    captured: dict[str, object] = {}
    _install_fake_litellm(captured)
    client = LLMClient(model="openai/gpt-4o-mini", api_key="test-key")

    async def _run() -> str:
        return await client.complete("hello", api_base="   ")

    result = asyncio.run(_run())
    assert result == "ok"

    forwarded = captured["kwargs"]
    assert isinstance(forwarded, dict)
    assert "api_base" not in forwarded


def test_complete_stream_keeps_non_empty_api_base() -> None:
    captured: dict[str, object] = {}
    _install_fake_litellm(captured)
    client = LLMClient(model="openai/gpt-4o-mini", api_key="test-key")

    async def _run() -> str:
        return await client.complete("hello", api_base="https://example.test/v1")

    result = asyncio.run(_run())
    assert result == "ok"

    forwarded = captured["kwargs"]
    assert isinstance(forwarded, dict)
    assert forwarded.get("api_base") == "https://example.test/v1"
