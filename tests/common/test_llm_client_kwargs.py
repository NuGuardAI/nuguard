from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from nuguard.common.llm_client import LLMClient, _is_reasoning_model


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


class _FakeAuthError(Exception): pass
class _FakeRateLimitError(Exception): pass
class _FakeServiceUnavailableError(Exception): pass
class _FakeBadRequestError(Exception): pass
class _FakeConnectionError(Exception): pass
class _FakeTimeout(Exception): pass
class _FakeUnsupportedParamsError(Exception): pass


def _install_fake_litellm(
    captured: dict[str, object],
    *,
    raise_unsupported_on_first: bool = False,
    raise_connection_error_times: int = 0,
) -> None:
    call_count = 0

    async def _acompletion(**kwargs):
        nonlocal call_count
        call_count += 1
        captured["kwargs"] = kwargs
        captured["call_count"] = call_count
        if raise_unsupported_on_first and call_count == 1:
            raise _FakeUnsupportedParamsError("temperature not supported")
        if call_count <= raise_connection_error_times:
            raise _FakeConnectionError("Connection error.")
        return _FakeStream(["ok"])

    fake_module = SimpleNamespace(
        acompletion=_acompletion,
        AuthenticationError=_FakeAuthError,
        RateLimitError=_FakeRateLimitError,
        ServiceUnavailableError=_FakeServiceUnavailableError,
        BadRequestError=_FakeBadRequestError,
        APIConnectionError=_FakeConnectionError,
        Timeout=_FakeTimeout,
        UnsupportedParamsError=_FakeUnsupportedParamsError,
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


# ---------------------------------------------------------------------------
# _is_reasoning_model
# ---------------------------------------------------------------------------


def test_is_reasoning_model_gpt5_variants() -> None:
    for model in ("openai/gpt-5", "openai/gpt-5-codex", "openai/gpt-5.1", "gpt-5"):
        assert _is_reasoning_model(model), f"Expected reasoning model: {model}"


def test_is_reasoning_model_o_series() -> None:
    for model in ("openai/o1", "openai/o1-mini", "openai/o3", "openai/o3-mini", "openai/o4-mini"):
        assert _is_reasoning_model(model), f"Expected reasoning model: {model}"


def test_is_reasoning_model_false_for_standard_models() -> None:
    for model in ("openai/gpt-4o", "openai/gpt-4o-mini", "openai/gpt-4-turbo", "gemini/gemini-2.0-flash"):
        assert not _is_reasoning_model(model), f"Should NOT be reasoning model: {model}"


# ---------------------------------------------------------------------------
# temperature stripping for reasoning models
# ---------------------------------------------------------------------------


def test_reasoning_model_strips_temperature_preemptively() -> None:
    captured: dict[str, object] = {}
    _install_fake_litellm(captured)
    client = LLMClient(model="openai/gpt-5", api_key="test-key")

    async def _run() -> str:
        return await client.complete("hello", temperature=0.0)

    result = asyncio.run(_run())
    assert result == "ok"
    assert "temperature" not in captured["kwargs"]


def test_reasoning_model_strips_top_p_preemptively() -> None:
    captured: dict[str, object] = {}
    _install_fake_litellm(captured)
    client = LLMClient(model="openai/o3-mini", api_key="test-key")

    async def _run() -> str:
        return await client.complete("hello", temperature=1.0, top_p=0.9)

    result = asyncio.run(_run())
    assert result == "ok"
    assert "temperature" not in captured["kwargs"]
    assert "top_p" not in captured["kwargs"]


def test_reasoning_model_skips_min_temperature_enforcement() -> None:
    captured: dict[str, object] = {}
    _install_fake_litellm(captured)
    # min_temperature would normally clamp temperature upward, but should be
    # skipped entirely for reasoning models
    client = LLMClient(model="openai/gpt-5", api_key="test-key", min_temperature=0.7)

    async def _run() -> str:
        return await client.complete("hello")

    result = asyncio.run(_run())
    assert result == "ok"
    assert "temperature" not in captured["kwargs"]


def test_standard_model_keeps_temperature() -> None:
    captured: dict[str, object] = {}
    _install_fake_litellm(captured)
    client = LLMClient(model="openai/gpt-4o", api_key="test-key")

    async def _run() -> str:
        return await client.complete("hello", temperature=0.2)

    result = asyncio.run(_run())
    assert result == "ok"
    assert captured["kwargs"]["temperature"] == 0.2


# ---------------------------------------------------------------------------
# UnsupportedParamsError — strip and retry fallback
# ---------------------------------------------------------------------------


def test_connection_error_retries_and_succeeds() -> None:
    captured: dict[str, object] = {}
    # Fail twice with connection errors, then succeed on the third attempt.
    _install_fake_litellm(captured, raise_connection_error_times=2)
    client = LLMClient(model="azure/gpt-5.4-mini", api_key="test-key")

    async def _run() -> str:
        return await client.complete("hello")

    result = asyncio.run(_run())
    assert result == "ok"
    assert captured["call_count"] == 3


def test_unsupported_params_error_strips_and_retries() -> None:
    captured: dict[str, object] = {}
    # First call raises UnsupportedParamsError; second call succeeds
    _install_fake_litellm(captured, raise_unsupported_on_first=True)
    # Use a non-reasoning model so the pre-emptive strip doesn't fire,
    # exercising the reactive handler
    client = LLMClient(model="openai/gpt-4o", api_key="test-key")

    async def _run() -> str:
        return await client.complete("hello", temperature=0.5)

    result = asyncio.run(_run())
    assert result == "ok"
    # Must have been called twice (first failed, second succeeded)
    assert captured["call_count"] == 2
    # temperature should be absent on the retry
    assert "temperature" not in captured["kwargs"]
