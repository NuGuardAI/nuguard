"""Tests for the headless ground-truth chat-endpoint fallback
(``sniff_chat_endpoint_headless`` / ``_find_chat_payload_key``) in
nuguard.common.browser_login.session — the Phase 3 fallback used by
nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint when
both the SBOM-ranked candidates and the blind HTTP probe fail.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from nuguard.common.browser_login.session import (
    _find_chat_payload_key,
    sniff_chat_endpoint_headless,
)
from nuguard.common.errors import BrowserLoginError


def test_find_chat_payload_key_top_level_string_match() -> None:
    body = {"prompt": "Hello", "session_id": "abc"}
    assert _find_chat_payload_key(body, "Hello") == ("prompt", False)


def test_find_chat_payload_key_nested_message_history() -> None:
    body = {"messages": [{"role": "user", "content": "Hello"}], "model": "gpt"}
    assert _find_chat_payload_key(body, "Hello") == ("messages", True)


def test_find_chat_payload_key_returns_none_when_message_absent() -> None:
    body = {"prompt": "something else entirely"}
    assert _find_chat_payload_key(body, "Hello") is None


class _FakeSession:
    """Stand-in for BrowserLoginSession — same async-context-manager shape,
    no real Playwright/browser involved."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def _navigate(self) -> None:
        return None

    async def _sniff_chat_request(self, message: str) -> tuple[str | None, dict | None]:
        return "https://target.example/api/chat", {"messages": [{"role": "user", "content": message}]}


@pytest.mark.asyncio
async def test_sniff_chat_endpoint_headless_returns_observed_endpoint() -> None:
    with patch(
        "nuguard.common.browser_login.session.BrowserLoginSession",
        new=_FakeSession,
    ):
        result = await sniff_chat_endpoint_headless("https://target.example", chat_message="Hello")

    assert result == ("/api/chat", "messages", True)


@pytest.mark.asyncio
async def test_sniff_chat_endpoint_headless_returns_none_when_nothing_captured() -> None:
    class _NoCaptureSession(_FakeSession):
        async def _sniff_chat_request(self, message: str) -> tuple[str | None, dict | None]:
            return None, None

    with patch(
        "nuguard.common.browser_login.session.BrowserLoginSession",
        new=_NoCaptureSession,
    ):
        result = await sniff_chat_endpoint_headless("https://target.example")

    assert result is None


@pytest.mark.asyncio
async def test_sniff_chat_endpoint_headless_swallows_browser_login_error() -> None:
    class _BoomSession(_FakeSession):
        async def __aenter__(self) -> "_BoomSession":
            raise BrowserLoginError("Chromium not installed.", step="browser_binary_missing")

    with patch(
        "nuguard.common.browser_login.session.BrowserLoginSession",
        new=_BoomSession,
    ):
        result = await sniff_chat_endpoint_headless("https://target.example")

    assert result is None


@pytest.mark.asyncio
async def test_sniff_chat_endpoint_headless_returns_none_when_message_field_unidentifiable() -> None:
    """The observed body doesn't contain the sent message verbatim (app
    transformed/wrapped it) — give up rather than guess a wrong field name."""

    class _UnrecognizableSession(_FakeSession):
        async def _sniff_chat_request(self, message: str) -> tuple[str | None, dict | None]:
            return "https://target.example/api/chat", {"payload": "totally different text"}

    with patch(
        "nuguard.common.browser_login.session.BrowserLoginSession",
        new=_UnrecognizableSession,
    ):
        result = await sniff_chat_endpoint_headless("https://target.example", chat_message="Hello")

    assert result is None
