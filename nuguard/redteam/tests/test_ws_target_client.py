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


@pytest.mark.asyncio
async def test_set_chat_endpoint_forces_reconnect_to_new_path():
    async def _path_echo_handler(ws) -> None:
        async for _ in ws:
            await ws.send(json.dumps({"response": ws.request.path}))

    async with serve(_path_echo_handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = WebSocketTargetClient(
            base_url=f"http://localhost:{port}",
            chat_path="/old",
            chat_response_key="response",
        )
        await client._connect()
        client.set_chat_endpoint("/new", payload_key="message")
        assert client._ws is None
        session = client.new_session("chain-1")
        text, _ = await client.send("hi", session)
        assert text == "/new"
        await client.aclose()


@pytest.mark.asyncio
async def test_set_path_param_substitutes_placeholder_in_chat_path():
    async def _path_echo_handler(ws) -> None:
        async for _ in ws:
            await ws.send(json.dumps({"response": ws.request.path}))

    async with serve(_path_echo_handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = WebSocketTargetClient(
            base_url=f"http://localhost:{port}",
            chat_path="/conversations/{id}/messages",
            chat_response_key="response",
        )
        client.set_path_param("id", "abc123")
        session = client.new_session("chain-1")
        text, _ = await client.send("hi", session)
        assert text == "/conversations/abc123/messages"
        await client.aclose()


@pytest.mark.asyncio
async def test_update_default_headers_forces_reconnect_with_new_headers():
    received_headers: dict[str, str] = {}

    async def _capture_handler(ws) -> None:
        received_headers.update(ws.request.headers)
        await ws.send(json.dumps({"response": "ok"}))

    async with serve(_capture_handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = WebSocketTargetClient(
            base_url=f"http://localhost:{port}",
            chat_path="/",
            chat_response_key="response",
        )
        await client._connect()
        client.update_default_headers({"Authorization": "Bearer new-token"})
        assert client._ws is None
        session = client.new_session("chain-1")
        await client.send("hi", session)
        assert received_headers.get("authorization") == "Bearer new-token"
        await client.aclose()


@pytest.mark.asyncio
async def test_invoke_endpoint_sends_direct_http_request_bypassing_websocket():
    """IDOR/BFLA/mass-assignment scenarios probe plain REST paths directly,
    even when the chat channel itself is WebSocket."""
    import httpx
    import respx

    base = "http://ws-app.test"
    client = WebSocketTargetClient(base_url=base, chat_path="/ws/chat")
    with respx.mock:
        respx.post(f"{base}/api/orders/2").mock(
            return_value=httpx.Response(200, json={"order_id": 2, "owner": "someone-else"})
        )
        status, text, body = await client.invoke_endpoint(
            "/api/orders/2", method="POST", body={"x": 1}
        )
    assert status == 200
    assert body == {"order_id": 2, "owner": "someone-else"}
    assert "someone-else" in text
    await client.aclose()


@pytest.mark.asyncio
async def test_invoke_endpoint_strips_auth_headers_when_requested():
    import httpx
    import respx

    base = "http://ws-app.test"
    client = WebSocketTargetClient(
        base_url=base, chat_path="/ws/chat", default_headers={"Authorization": "Bearer tok"}
    )
    captured: dict[str, str] = {}

    def _responder(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200, json={})

    with respx.mock:
        respx.post(f"{base}/api/resource").mock(side_effect=_responder)
        await client.invoke_endpoint("/api/resource", method="POST", strip_auth=True)
    assert "authorization" not in captured
    await client.aclose()
