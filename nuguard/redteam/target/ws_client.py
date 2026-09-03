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

import httpx

from nuguard.common.errors import TargetUnavailableError
from nuguard.common.logging import get_logger
from nuguard.common.transport import strip_known_boilerplate

from .client import _substitute_path_params
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
        # :name/{name} placeholder bindings applied to _chat_path before connecting
        # — see TargetAppClient.set_path_param() / the two-step chat flow.
        self._path_param_values: dict[str, str] = {}
        # Header names carrying auth material, tracked for invoke_endpoint(strip_auth=True).
        self._auth_header_names: set[str] = set(default_headers or {})
        # Separate plain-HTTP client for invoke_endpoint() direct-path probes
        # (IDOR/BFLA/mass-assignment scenarios) — independent of the WS chat
        # socket, since WS-chat targets often still expose plain REST routes
        # for non-chat resources. Created lazily on first use.
        self._http_client: httpx.AsyncClient | None = None
        # Populated by build_target_app_client() with human-readable notes.
        self.resolution_notes: list[str] = []

    def new_session(self, chain_id: str) -> AttackSession:
        """Create a new AttackSession for the given chain."""
        return AttackSession(
            session_id=str(uuid.uuid4()),
            target_url=self.base_url,
            chain_id=chain_id,
        )

    @property
    def chat_path(self) -> str:
        """The currently configured chat endpoint path."""
        return self._chat_path

    @property
    def path_param_values(self) -> dict[str, str]:
        """Currently bound ``:name``/``{name}`` path-param values (copy)."""
        return dict(self._path_param_values)

    def set_chat_endpoint(
        self,
        path: str,
        payload_key: str,
        payload_list: bool = False,
        response_key: str | None = None,
    ) -> None:
        """Replace the configured chat endpoint without rebuilding the client.

        Mirrors :meth:`TargetAppClient.set_chat_endpoint` — used for endpoint
        rotation when the primary endpoint is broken. Closes the current
        socket (if any) so the next :meth:`send` reconnects to the new path.
        """
        _log.info(
            "WebSocketTargetClient.set_chat_endpoint: rotating from %s → %s",
            self._chat_path, path,
        )
        self._chat_path = path
        self._chat_payload_key = payload_key
        self._chat_payload_list = payload_list
        if response_key is not None:
            self._chat_response_key = response_key
        self._path_param_values = {}
        self._consecutive_errors = 0
        self._ws = None  # force reconnect to the new path on the next send()

    def set_path_param(self, name: str, value: str) -> None:
        """Bind *value* for a ``:name``/``{name}`` placeholder in the chat path.

        Mirrors :meth:`TargetAppClient.set_path_param` — see the two-step chat
        flow docs referenced there.
        """
        self._path_param_values[name] = value

    def update_default_headers(self, headers: dict[str, str] | None) -> None:
        """Merge headers into the default headers used on the next (re)connect.

        Mirrors :meth:`TargetAppClient.update_default_headers`. Since headers
        are only sent during the WS handshake, this closes the current socket
        so the next :meth:`send` reconnects with the updated headers.
        """
        if not headers:
            return
        self._default_headers = {**self._default_headers, **headers}
        self._auth_header_names.update(headers)
        self._ws = None  # force reconnect with the updated headers

    def _ensure_http_client(self) -> httpx.AsyncClient:
        """Lazily create the plain-HTTP client used by :meth:`invoke_endpoint`."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self._timeout),
                headers={"User-Agent": "nuguard-redteam/1.0", **self._default_headers},
                follow_redirects=True,
            )
        return self._http_client

    async def invoke_endpoint(
        self,
        path: str,
        method: str = "POST",
        body: dict | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        strip_auth: bool = False,
    ) -> tuple[int, str, dict]:
        """Send a direct HTTP request to *path*, bypassing the WS chat socket.

        WebSocket-chat targets often still expose plain REST endpoints for
        non-chat resources (orders, accounts, ...) — direct-HTTP attack
        scenarios (IDOR/BFLA/mass-assignment) probe those over regular HTTP.
        Mirrors :meth:`TargetAppClient.invoke_endpoint`'s
        ``(status_code, response_text, response_json)`` contract; does NOT
        raise on 4xx/5xx.
        """
        client = self._ensure_http_client()
        try:
            if strip_auth and self._auth_header_names:
                request = client.build_request(
                    method=method.upper(), url=path, json=body, params=params,
                )
                for name in self._auth_header_names:
                    if name in request.headers:
                        del request.headers[name]
                if extra_headers:
                    request.headers.update(extra_headers)
                resp = await client.send(request)
            else:
                resp = await client.request(
                    method=method.upper(), url=path, json=body, params=params,
                    headers=extra_headers or {},
                )
            try:
                json_body: dict = resp.json()
            except Exception:
                json_body = {}
            return resp.status_code, strip_known_boilerplate(resp.text), json_body
        except Exception as exc:
            label = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            _log.warning("WebSocketTargetClient.invoke_endpoint: %s %s failed: %s", method, path, label)
            return 0, f"[REQUEST_ERROR: {label}]", {}

    async def _connect(self) -> None:
        import websockets  # noqa: PLC0415 — optional dep, imported lazily

        resolved_path, _missing = _substitute_path_params(self._chat_path, self._path_param_values)
        url = to_ws_url(self.base_url) + resolved_path
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
        """Close the underlying WebSocket connection and HTTP client, if open."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def __aenter__(self) -> "WebSocketTargetClient":
        await self._ensure_connected()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()
