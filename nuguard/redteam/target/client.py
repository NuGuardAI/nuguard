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

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": "nuguard-redteam/1.0"},
            follow_redirects=True,
        )

    async def send(self, payload: str, session: AttackSession) -> tuple[str, list[dict]]:
        """Send a prompt payload to the target and return (response_text, tool_calls)."""
        # Build conversation history from session turns
        messages = []
        for turn in session.turns:
            messages.append({"role": "user", "content": turn.prompt})
            messages.append({"role": "assistant", "content": turn.response})
        messages.append({"role": "user", "content": payload})

        body: dict[str, Any] = {"messages": messages}

        try:
            resp = await self._client.post("/chat", json=body)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            _log.warning("Target HTTP error %s: %s", exc.response.status_code, exc)
            return f"[HTTP {exc.response.status_code}]", []
        except Exception as exc:
            _log.warning("Target request failed: %s", exc)
            return f"[REQUEST_ERROR: {exc}]", []

        # Extract response text
        text = (
            data.get("response")
            or data.get("content")
            or data.get("message", {}).get("content", "")
            or ""
        )
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
