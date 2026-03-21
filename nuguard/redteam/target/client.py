"""HTTP client for sending adversarial requests to the target application."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from .session import AttackSession

_log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class TargetAppClient:
    """Thin httpx wrapper for sending chat/completion requests to the target."""

    def __init__(
        self,
        base_url: str,
        chat_path: str = "/chat",
        chat_payload_key: str = "message",
        chat_payload_list: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._chat_path = chat_path
        self._chat_payload_key = chat_payload_key
        self._chat_payload_list = chat_payload_list
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": "nuguard-redteam/1.0"},
            follow_redirects=True,
        )

    async def send(self, payload: str, session: AttackSession) -> tuple[str, list[dict]]:
        """Send a prompt payload to the target and return (response_text, tool_calls)."""
        value: Any = [payload] if self._chat_payload_list else payload
        body: dict[str, Any] = {self._chat_payload_key: value}

        try:
            resp = await self._client.post(self._chat_path, json=body)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            _log.warning("Target HTTP error %s: %s", exc.response.status_code, exc)
            return f"[HTTP {exc.response.status_code}]", []
        except Exception as exc:
            _log.warning("Target request failed: %s", exc)
            return f"[REQUEST_ERROR: {exc}]", []

        # Extract response text — try common response shapes
        text = (
            data.get("response")
            or data.get("content")
            or data.get("message", {}).get("content", "")
            or ""
        )
        # Handle list-of-messages response (e.g. openai-cs-agents-demo)
        if not text and isinstance(data.get("messages"), list):
            msgs = data["messages"]
            if msgs and isinstance(msgs[-1], dict):
                text = msgs[-1].get("content") or msgs[-1].get("text") or ""
        if not text and isinstance(data, str):
            text = data

        # Extract tool calls
        tool_calls: list[dict] = []
        raw_calls = (
            data.get("tool_calls")
            or data.get("function_calls")
            or data.get("message", {}).get("tool_calls", [])
            or []
        )
        if isinstance(raw_calls, list):
            tool_calls = raw_calls

        return str(text), tool_calls

    async def invoke_endpoint(
        self,
        path: str,
        method: str = "POST",
        body: dict | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, str, dict]:
        """Send a direct HTTP request to a specific path.

        Returns (status_code, response_text, response_json).  Does NOT raise on
        4xx/5xx — callers inspect the status code to determine attack success.
        """
        try:
            resp = await self._client.request(
                method=method.upper(),
                url=path,
                json=body,
                params=params,
                headers=extra_headers or {},
            )
            try:
                json_body: dict = resp.json()
            except Exception:
                json_body = {}
            return resp.status_code, resp.text, json_body
        except Exception as exc:
            _log.warning("Direct request %s %s failed: %s", method, path, exc)
            return 0, f"[REQUEST_ERROR: {exc}]", {}

    async def send_raw(self, path: str, body: dict) -> dict:
        """Send a raw POST to any path; returns parsed JSON response."""
        try:
            resp = await self._client.post(path, json=body)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            _log.warning("Raw request to %s failed: %s", path, exc)
            return {}

    async def health_check(self) -> bool:
        """Return True if the target responds to a health check."""
        try:
            resp = await self._client.get("/health", timeout=5.0)
            return resp.is_success
        except Exception:
            return False

    def new_session(self, chain_id: str) -> AttackSession:
        """Create a new AttackSession for the given chain."""
        return AttackSession(
            session_id=str(uuid.uuid4()),
            target_url=self.base_url,
            chain_id=chain_id,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> "TargetAppClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()
