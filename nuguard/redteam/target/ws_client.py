"""WebSocket client for sending adversarial requests to a WS-based target application.

Companion to :class:`~nuguard.redteam.target.client.TargetAppClient` (HTTP).
Exposes the same ``send(payload, session, extra_headers=None)`` contract so
callers (``BehaviorRunner``, ``RedteamOrchestrator``) can use either client
interchangeably behind ``async with build_target_app_client(...) as client:``.

See ``imp_docs/websocket_support.md`` (Layer 2) for background.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Any

from nuguard.common.errors import TargetUnavailableError
from nuguard.common.logging import get_logger
from nuguard.common.transport import strip_known_boilerplate

from .session import AttackSession

if TYPE_CHECKING:
    import websockets.asyncio.client

_log = get_logger(__name__)

DEFAULT_TIMEOUT = 30.0
MAX_CONSECUTIVE_ERRORS = 3
# Cap on drained server-push messages per send() call (typing indicators,
# partial tokens) before giving up on a "complete" response.
MAX_DRAIN_MESSAGES = 50


def to_ws_url(base_url: str) -> str:
    """Convert an http(s):// base URL to its ws(s):// equivalent."""
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://"):]
    if base_url.startswith("http://"):
        return "ws://" + base_url[len("http://"):]
    return base_url


class WebSocketTargetClient:
    """Sends chat payloads over a persistent WebSocket connection.

    The socket is opened lazily on first use (or via ``__aenter__``) and kept
    open across all turns of a scenario — closing only in ``__aexit__`` /
    :meth:`aclose`, or when the connection drops and is transparently
    reopened on the next :meth:`send`.
    """

    def __init__(
        self,
        base_url: str,
        chat_path: str = "/ws",
        chat_payload_key: str = "message",
        chat_payload_list: bool = False,
        chat_response_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        default_headers: dict[str, str] | None = None,
        chat_payload_extras: dict[str, Any] | None = None,
        max_consecutive_errors: int = MAX_CONSECUTIVE_ERRORS,
        # First-message auth pattern: sent immediately after connecting, before
        # any chat payload, e.g. {"type": "auth", "token": "..."}.
        ws_auth_message: dict[str, Any] | None = None,
        # Key that marks a message as the final chunk of a response — messages
        # without this key (truthy) are drained/ignored while awaiting completion.
        ws_response_complete_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._chat_path = chat_path
        self._chat_payload_key = chat_payload_key
        self._chat_payload_list = chat_payload_list
        self._chat_response_key = chat_response_key
        self._timeout = timeout
        self._default_headers = default_headers or {}
        self._chat_payload_extras: dict[str, Any] = chat_payload_extras or {}
        self._max_consecutive_errors = max_consecutive_errors
        self._ws_auth_message = ws_auth_message
        self._ws_response_complete_key = ws_response_complete_key
        self._ws: "websockets.asyncio.client.ClientConnection | None" = None
        self._consecutive_errors = 0
        # Populated by build_target_app_client() with human-readable notes.
        self.resolution_notes: list[str] = []

    def new_session(self, chain_id: str) -> AttackSession:
        """Create a new AttackSession for the given chain."""
        return AttackSession(
            session_id=str(uuid.uuid4()),
            target_url=self.base_url,
            chain_id=chain_id,
        )

    async def _connect(self) -> None:
        import websockets  # noqa: PLC0415 — optional dep, imported lazily

        url = to_ws_url(self.base_url) + self._chat_path
        self._ws = await websockets.connect(
            url,
            additional_headers=self._default_headers or None,
            open_timeout=self._timeout,
        )
        if self._ws_auth_message is not None:
            await self._ws.send(json.dumps(self._ws_auth_message))

    async def _ensure_connected(self) -> None:
        if self._ws is None:
            await self._connect()

    def _build_body(self, payload: str) -> dict[str, Any]:
        value: object = [payload] if self._chat_payload_list else payload
        body: dict[str, Any] = {self._chat_payload_key: value}
        body.update(self._chat_payload_extras)
        return body

    def _extract_text(self, data: object) -> str:
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            if self._chat_response_key and isinstance(data.get(self._chat_response_key), str):
                return data[self._chat_response_key]
            for value in data.values():
                if isinstance(value, str):
                    return value
        return json.dumps(data)

    async def send(
        self,
        payload: str,
        session: AttackSession,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[str, list[dict]]:
        """Send a prompt payload over the WebSocket and return (response_text, tool_calls).

        Drains server-push messages (typing indicators, partial tokens) until a
        message matching ``ws_response_complete_key`` arrives, or until
        ``MAX_DRAIN_MESSAGES`` is reached. Reconnects transparently on a dropped
        connection and raises :class:`TargetUnavailableError` after
        ``max_consecutive_errors`` consecutive failures.
        """
        import websockets  # noqa: PLC0415

        try:
            await self._ensure_connected()
            assert self._ws is not None
            await self._ws.send(json.dumps(self._build_body(payload)))

            text = ""
            for _ in range(MAX_DRAIN_MESSAGES):
                raw = await asyncio.wait_for(self._ws.recv(), timeout=self._timeout)
                try:
                    data: object = json.loads(raw)
                except (TypeError, ValueError):
                    data = raw
                if (
                    self._ws_response_complete_key
                    and isinstance(data, dict)
                    and not data.get(self._ws_response_complete_key)
                ):
                    continue
                text = self._extract_text(data)
                break

            self._consecutive_errors = 0
            return strip_known_boilerplate(text), []
        except (websockets.exceptions.ConnectionClosed, OSError, asyncio.TimeoutError) as exc:
            self._ws = None  # force reconnect on the next send()
            self._consecutive_errors += 1
            _log.warning(
                "WebSocket chat error (%s) — consecutive=%d/%d",
                exc, self._consecutive_errors, self._max_consecutive_errors,
            )
            if self._consecutive_errors >= self._max_consecutive_errors:
                raise TargetUnavailableError(
                    f"WebSocket target failed {self._consecutive_errors} consecutive times "
                    f"(last: {exc}) — aborting scan to avoid hammering a broken endpoint."
                ) from exc
            return "", []

    async def aclose(self) -> None:
        """Close the underlying WebSocket connection, if open."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def __aenter__(self) -> "WebSocketTargetClient":
        await self._ensure_connected()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()
