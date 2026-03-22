"""HTTP client for sending adversarial requests to the target application."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from .session import AttackSession

_log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
MAX_CONSECUTIVE_ERRORS = 3


class TargetUnavailableError(Exception):
    """Raised when the target chat endpoint returns too many consecutive errors.

    Signals the orchestrator to abort remaining scenarios rather than
    hammering a broken or unreachable endpoint.
    """


class TargetAppClient:
    """Thin httpx wrapper for sending chat/completion requests to the target."""

    def __init__(
        self,
        base_url: str,
        chat_path: str = "/chat",
        chat_payload_key: str = "message",
        chat_payload_list: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
        max_consecutive_errors: int = MAX_CONSECUTIVE_ERRORS,
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
        self._max_consecutive_errors = max_consecutive_errors
        # Circuit breaker: count consecutive errors on the chat endpoint.
        # Reset to 0 on any successful response.
        self._consecutive_errors: int = 0

    def _record_chat_error(self, label: str) -> None:
        """Increment the consecutive-error counter and open the circuit when the threshold is hit."""
        self._consecutive_errors += 1
        _log.warning(
            "Chat endpoint error (%s) — consecutive=%d/%d",
            label,
            self._consecutive_errors,
            self._max_consecutive_errors,
        )
        if self._consecutive_errors >= self._max_consecutive_errors:
            raise TargetUnavailableError(
                f"Chat endpoint returned {self._consecutive_errors} consecutive errors "
                f"(last: {label}) — aborting scan to avoid hammering a broken endpoint."
            )

    def _record_chat_success(self) -> None:
        """Reset the consecutive-error counter after a successful chat response."""
        if self._consecutive_errors:
            _log.debug("Chat endpoint recovered — resetting error counter")
        self._consecutive_errors = 0

    async def send(self, payload: str, session: AttackSession) -> tuple[str, list[dict]]:
        """Send a prompt payload to the target and return (response_text, tool_calls).

        Raises:
            TargetUnavailableError: after MAX_CONSECUTIVE_ERRORS consecutive 5xx
                or network errors on the chat endpoint.  4xx responses (validation
                errors, auth rejections, rate limits) do not count — the target is
                alive and responding; it simply rejected our specific payload.
        """
        value: Any = [payload] if self._chat_payload_list else payload
        body: dict[str, Any] = {self._chat_payload_key: value}

        try:
            resp = await self._client.post(self._chat_path, json=body)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            body_preview = exc.response.text[:300] if exc.response.text else ""
            _log.warning(
                "Target HTTP %s  url=%s  body=%r",
                status, exc.request.url, body_preview,
            )
            # 4xx responses mean the target IS reachable — it actively rejected our
            # payload (validation error, auth failure, rate limit, etc.).  Do NOT
            # count these toward the circuit breaker; the target is functioning.
            # Only 5xx server errors indicate genuine unavailability.
            if status >= 500:
                self._record_chat_error(f"HTTP {status}")
            else:
                self._record_chat_success()
            return f"[HTTP {status}]", []
        except Exception as exc:
            label = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            _log.warning(
                "Target request failed  %s  url=%s%s",
                label, self.base_url, self._chat_path,
            )
            self._record_chat_error(label[:120])
            return f"[REQUEST_ERROR: {label}]", []

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

        self._record_chat_success()
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
            label = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            _log.warning("Direct request %s %s failed: %s", method, path, label)
            # Network-level failures (connection refused, DNS error, timeout) on
            # direct endpoint probes also count toward the circuit breaker because
            # they indicate the target is unreachable, not just rejecting our probe.
            self._record_chat_error(label[:120])
            return 0, f"[REQUEST_ERROR: {label}]", {}

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
