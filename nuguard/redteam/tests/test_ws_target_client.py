"""Tests for WebSocketTargetClient (nuguard.redteam.target.ws_client).

Spins up a real local `websockets` server so tests exercise the actual
connect/send/recv/close lifecycle rather than mocking the library.
"""
from __future__ import annotations

import json

import pytest
from websockets.asyncio.server import serve

from nuguard.common.errors import TargetUnavailableError
from nuguard.redteam.target.ws_client import WebSocketTargetClient


async def _echo_handler(ws) -> None:
    async for raw in ws:
        body = json.loads(raw)
        await ws.send(json.dumps({"response": f"echo: {body.get('message')}"}))


async def _draining_handler(ws) -> None:
    """Sends two partial/typing pushes before the final complete message."""
    async for raw in ws:
        await ws.send(json.dumps({"type": "typing", "done": False}))
        await ws.send(json.dumps({"type": "typing", "done": False}))
        await ws.send(json.dumps({"type": "message", "done": True, "text": "final answer"}))


async def _auth_first_message_handler(ws) -> None:
    first = json.loads(await ws.recv())
    if first.get("type") != "auth" or first.get("token") != "secret-token":
        await ws.close(code=4001, reason="auth required")
        return
    async for raw in ws:
        body = json.loads(raw)
        await ws.send(json.dumps({"response": f"authed: {body.get('message')}"}))


@pytest.mark.asyncio
async def test_send_receives_echoed_response():
    async with serve(_echo_handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = WebSocketTargetClient(
            base_url=f"http://localhost:{port}",
            chat_path="/",
            chat_response_key="response",
        )
        async with client as c:
            session = c.new_session("chain-1")
            text, tool_calls = await c.send("hello", session)
        assert text == "echo: hello"
        assert tool_calls == []


@pytest.mark.asyncio
async def test_send_drains_partial_messages_until_complete():
    async with serve(_draining_handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = WebSocketTargetClient(
            base_url=f"http://localhost:{port}",
            chat_path="/",
            chat_response_key="text",
            ws_response_complete_key="done",
        )
        async with client as c:
            session = c.new_session("chain-1")
            text, _ = await c.send("hello", session)
        assert text == "final answer"


@pytest.mark.asyncio
async def test_send_uses_first_message_auth():
    async with serve(_auth_first_message_handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = WebSocketTargetClient(
            base_url=f"http://localhost:{port}",
            chat_path="/",
            chat_response_key="response",
            ws_auth_message={"type": "auth", "token": "secret-token"},
        )
        async with client as c:
            session = c.new_session("chain-1")
            text, _ = await c.send("hi", session)
        assert text == "authed: hi"


@pytest.mark.asyncio
async def test_send_raises_after_max_consecutive_connect_failures():
    client = WebSocketTargetClient(
        base_url="http://localhost:1",  # nothing listening
        chat_path="/",
        max_consecutive_errors=2,
    )
    session = client.new_session("chain-1")
    text, _ = await client.send("hello", session)
    assert text == ""
    with pytest.raises(TargetUnavailableError):
        await client.send("hello", session)


@pytest.mark.asyncio
async def test_reconnects_after_server_closes_connection():
    async with serve(_echo_handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = WebSocketTargetClient(
            base_url=f"http://localhost:{port}",
            chat_path="/",
            chat_response_key="response",
        )
        # Force a stale/closed socket into the client. The next send() should
        # detect the closed connection, clear it, and reconnect cleanly on the
        # following call rather than raising immediately.
        await client._connect()
        await client._ws.close()
        session = client.new_session("chain-1")
        first_text, _ = await client.send("hello", session)
        assert first_text == ""
        second_text, _ = await client.send("hello", session)
        assert second_text == "echo: hello"
        await client.aclose()
