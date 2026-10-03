"""Tests for target capability classification and isolation verification."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nuguard.redteam.campaign.transport.capabilities import (
    SessionMode,
    classify_target,
    reset_branch_session,
    verify_isolation,
)
from nuguard.redteam.target.client import TargetAppClient
from nuguard.redteam.target.framework_adapters import GoogleADKAdapter


def _client(**kw):
    return TargetAppClient(base_url="http://t", **kw)


def test_adk_adapter_declares_server_session() -> None:
    c = _client()
    c._framework_adapter = GoogleADKAdapter(app_name="a")  # type: ignore[assignment]
    caps = classify_target(c)
    assert (caps.mode, caps.supports_reset, caps.source) == (SessionMode.SERVER_SESSION, True, "adapter")


def test_history_key_is_client_history() -> None:
    caps = classify_target(_client(chat_payload_key="messages", chat_payload_list=True))
    assert caps.mode == SessionMode.CLIENT_HISTORY


def test_history_token_template_is_client_history() -> None:
    caps = classify_target(
        _client(chat_payload_extras={"q": "{{message}}", "h": "{{history}}"})
    )
    assert caps.mode == SessionMode.CLIENT_HISTORY


def test_session_token_or_templated_path_is_server_session() -> None:
    assert classify_target(
        _client(chat_payload_extras={"q": "{{message}}", "sid": "{{session_id}}"})
    ).mode == SessionMode.SERVER_SESSION
    assert classify_target(_client(chat_path="/s/{sid}/chat")).mode == SessionMode.SERVER_SESSION


def test_plain_chat_is_stateless() -> None:
    assert classify_target(_client()).mode == SessionMode.STATELESS


def test_websocket_is_single_branch() -> None:
    class WebSocketTargetClient:  # name-matched, avoids opening a socket
        pass

    caps = classify_target(WebSocketTargetClient())
    assert caps.parallel_branches is False and caps.supports_reset is False


def test_operator_claim_overrides_detected_reset() -> None:
    caps = classify_target(_client()).with_operator_reset_claim(False)
    assert caps.supports_reset is False and caps.source == "operator"
    assert classify_target(_client()).with_operator_reset_claim(None).source == "payload_config"


@pytest.mark.asyncio
async def test_reset_branch_session_clears_adapter_cache() -> None:
    calls: list[str] = []
    c = SimpleNamespace(_framework_adapter=SimpleNamespace(reset_session=calls.append))
    await reset_branch_session(c, "branch-1")
    assert calls == ["branch-1"]
    await reset_branch_session(SimpleNamespace(_framework_adapter=None), "x")  # no-op


class _Target:
    """Fake target with per-conversation memory (or none)."""

    def __init__(self, leaky: bool = False, remembers: bool = True) -> None:
        self.leaky, self.remembers = leaky, remembers
        self.shared: str = ""

    def conversation(self):
        memory: list[str] = []

        async def send(text: str) -> str:
            if "remember the harmless code word" in text:
                word = text.rsplit(" ", 1)[-1].rstrip(".")
                if self.remembers:
                    (self.shared if False else memory).append(word)
                    if self.leaky:
                        self.shared = word
                return "ok"
            known = memory[0] if memory else (self.shared if self.leaky else "")
            return f"you said {known}" if known else "nothing"

        return send


@pytest.mark.asyncio
async def test_isolation_verified_when_fresh_branch_cannot_recall() -> None:
    t = _Target()
    assert await verify_isolation(t.conversation(), t.conversation()) is True


@pytest.mark.asyncio
async def test_isolation_false_when_state_leaks_across_branches() -> None:
    t = _Target(leaky=True)
    assert await verify_isolation(t.conversation(), t.conversation()) is False


@pytest.mark.asyncio
async def test_isolation_inconclusive_without_positive_control() -> None:
    t = _Target(remembers=False)
    assert await verify_isolation(t.conversation(), t.conversation()) is None


def test_session_builder_passes_framework_adapter_through() -> None:
    """build_target_app_client_from_session drops adapters unless one is passed
    explicitly (the legacy orchestrator never passes one) — campaign mode does."""
    from nuguard.common.target_client_builder import build_target_app_client_from_session
    from nuguard.common.session_resolver import TargetSessionConfig

    session = TargetSessionConfig(
        base_url="http://t",
        chat_path="/run",
        chat_payload_key="message",
        chat_payload_list=False,
        chat_payload_extras={},
        chat_response_key=None,
        auth_session=None,
    )
    adapter = GoogleADKAdapter(app_name="a")
    assert getattr(build_target_app_client_from_session(session), "_framework_adapter", "x") is None
    c = build_target_app_client_from_session(session, framework_adapter=adapter)
    assert c._framework_adapter is adapter  # type: ignore[union-attr]
