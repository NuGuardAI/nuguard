"""HTTP client for sending adversarial requests to the target application."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

import httpx

from nuguard.common.errors import TargetUnavailableError  # noqa: F401 — re-exported for callers

from .session import AttackSession

if TYPE_CHECKING:
    from .framework_adapters import FrameworkAdapter

_log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


def _extract_nested_key(data: dict[str, Any], key_path: str) -> Any:
    """Extract a value from a nested dict/list using a flexible path notation.

    Supports:
    - Dot notation: ``"a.b.c"``
    - Bracket array index: ``"outputs[0].text"``
    - Numeric path segment: ``"outputs.0.text"``
    - List spread: when a list is encountered without an explicit index,
      traversal continues across all items; non-null results are returned
      as a list (single element is unwrapped to the bare value).

    Examples::

        _extract_nested_key({"a": {"b": "val"}}, "a.b")            # → "val"
        _extract_nested_key({"outputs": [{"text": "hi"}]}, "outputs[0].text")  # → "hi"
        _extract_nested_key({"outputs": [{"text": "hi"}]}, "outputs.0.text")   # → "hi"
        _extract_nested_key({"outputs": [{"text": "a"}, {"text": "b"}]}, "outputs.text")  # → ["a", "b"]
    """
    # Tokenize: split on '.' then parse optional bracket index from each segment.
    # e.g. "outputs[0].text" → [("outputs", 0), ("text", None)]
    tokens: list[tuple[str, int | None]] = []
    for raw_part in key_path.split("."):
        m = re.fullmatch(r"(\w+)\[(\d+)\]", raw_part)
        if m:
            tokens.append((m.group(1), int(m.group(2))))
        else:
            tokens.append((raw_part, None))

    current: Any = data
    for key, idx in tokens:
        if current is None:
            return None

        # Numeric-only key → treat as list index
        if key.isdigit():
            if isinstance(current, list):
                i = int(key)
                current = current[i] if i < len(current) else None
            else:
                return None
        elif isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list):
            # List spread: collect the named key from each dict item
            results = [item.get(key) for item in current if isinstance(item, dict)]
            results = [r for r in results if r is not None]
            current = results[0] if len(results) == 1 else (results if results else None)
        else:
            return None

        # Apply explicit bracket array index if present
        if idx is not None:
            if isinstance(current, list):
                current = current[idx] if idx < len(current) else None
            else:
                return None

    return current
MAX_CONSECUTIVE_ERRORS = 3
DEFAULT_MAX_429_RETRIES = 2
DEFAULT_429_BACKOFF_BASE_SECONDS = 0.5
DEFAULT_429_BACKOFF_CAP_SECONDS = 5.0


class TargetAppClient:
    """Thin httpx wrapper for sending chat/completion requests to the target."""

    def __init__(
        self,
        base_url: str,
        chat_path: str = "/chat",
        chat_payload_key: str = "message",
        chat_payload_list: bool = False,
        chat_payload_format: str = "json",
        chat_response_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_consecutive_errors: int = MAX_CONSECUTIVE_ERRORS,
        max_429_retries: int = DEFAULT_MAX_429_RETRIES,
        retry_429_backoff_base_seconds: float = DEFAULT_429_BACKOFF_BASE_SECONDS,
        retry_429_backoff_cap_seconds: float = DEFAULT_429_BACKOFF_CAP_SECONDS,
        default_headers: dict[str, str] | None = None,
        framework_adapter: "FrameworkAdapter | None" = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._chat_path = chat_path
        self._chat_payload_key = chat_payload_key
        self._chat_payload_list = chat_payload_list
        self._chat_payload_format = (
            chat_payload_format if chat_payload_format in ("json", "form") else "json"
        )
        self._chat_response_key = chat_response_key  # explicit key overrides auto-detection
        # Lazily-detected response key: set on the first successful dict response when
        # no explicit key is configured.  Avoids returning raw JSON to judges.
        self._detected_response_key: str | None = None
        # Merge caller-supplied headers (e.g. auth) on top of the nuguard defaults.
        # These apply to every request made by this client instance.
        merged_headers: dict[str, str] = {"User-Agent": "nuguard-redteam/1.0"}
        if default_headers:
            merged_headers.update(default_headers)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            headers=merged_headers,
            follow_redirects=True,
        )
        self._max_consecutive_errors = max_consecutive_errors
        self._max_429_retries = max(0, max_429_retries)
        self._retry_429_backoff_base = max(0.01, retry_429_backoff_base_seconds)
        self._retry_429_backoff_cap = max(
            self._retry_429_backoff_base,
            retry_429_backoff_cap_seconds,
        )
        # Circuit breaker: count consecutive errors on the chat endpoint.
        # Reset to 0 on any successful response.
        self._consecutive_errors: int = 0
        # Session/conversation ID forwarding: if the app returns a session
        # or conversation identifier in its response, store it here and
        # include it in subsequent request bodies so multi-turn conversations
        # are correlated on the server side.
        self._session_context: dict[str, Any] = {}
        # Optional framework-specific adapter (e.g. Google ADK).  When set,
        # send() delegates body construction and response parsing to the adapter.
        self._framework_adapter: FrameworkAdapter | None = framework_adapter

    def _parse_retry_after_seconds(self, value: str | None) -> float | None:
        if not value:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return max(0.0, float(stripped))
        except ValueError:
            pass
        try:
            when = parsedate_to_datetime(stripped)
            now = datetime.now(timezone.utc)
            return max(0.0, (when - now).total_seconds())
        except Exception:
            return None

    def _retry_delay_seconds(self, headers: Any, body_text: str, attempt: int) -> float:
        retry_after = None
        if headers is not None:
            retry_after = self._parse_retry_after_seconds(headers.get("Retry-After"))
        if retry_after is None and body_text:
            try:
                parsed = json.loads(body_text)
                retry_after_ms = parsed.get("retryAfterMs")
                if isinstance(retry_after_ms, (int, float)):
                    retry_after = max(0.0, float(retry_after_ms) / 1000.0)
            except Exception:
                retry_after = None

        fallback = min(
            self._retry_429_backoff_base * (2 ** max(0, attempt)),
            self._retry_429_backoff_cap,
        )
        jitter = random.uniform(0.0, fallback * 0.25)
        delay = retry_after if retry_after is not None else fallback + jitter
        return min(max(0.01, delay), self._retry_429_backoff_cap)

    def _detect_response_key(self, data: dict[str, Any], sent_payload: str) -> str | None:
        """Heuristically identify which top-level key contains the agent's text response.

        Runs once on the first non-empty dict response when no explicit
        ``chat_response_key`` is configured.  The detected key is cached in
        ``self._detected_response_key`` and reused for all subsequent calls.

        Algorithm:
        1. Collect all top-level keys whose value is a non-empty string.
        2. Skip keys whose value is an echo of the sent payload (input reflection).
        3. Skip metadata-looking keys (IDs, timestamps, status codes).
        4. Rank remaining candidates by string length — longest → most informative.
        5. Return the top-ranked key, or ``None`` when no candidate qualifies.

        Args:
            data: Parsed JSON response from the target.
            sent_payload: The message that was sent, used to detect input echoes.

        Returns:
            The detected key string, or ``None``.
        """
        _META_KEYS = frozenset({
            "id", "session_id", "conversation_id", "thread_id", "chat_id",
            "status", "error", "code", "type", "kind", "version",
            "timestamp", "created_at", "updated_at",
        })
        _MIN_LENGTH = 30  # strings shorter than this are unlikely to be the response

        payload_lower = sent_payload.strip().lower()

        candidates: list[tuple[int, str]] = []
        for key, value in data.items():
            if key.lower() in _META_KEYS:
                continue
            if isinstance(value, str):
                if len(value) < _MIN_LENGTH:
                    continue
                # Skip if this is just an echo/reflection of the input.
                if payload_lower and value.strip().lower().startswith(payload_lower[:40]):
                    continue
                candidates.append((len(value), key))
            elif isinstance(value, list):
                # e.g. "phrases": ["...the original input..."] — skip echoes.
                joined = " ".join(str(v) for v in value if isinstance(v, str))
                if payload_lower and payload_lower[:60] in joined.lower():
                    continue
                # If it's a list of longer strings (not the input), consider it.
                if joined and len(joined) >= _MIN_LENGTH:
                    candidates.append((len(joined), key))

        if not candidates:
            return None

        best_key = max(candidates, key=lambda t: t[0])[1]
        _log.info(
            "TargetAppClient: auto-detected response key '%s' from %s (candidates: %s)",
            best_key,
            self._chat_path,
            [k for _, k in sorted(candidates, reverse=True)],
        )
        return best_key

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

    def reset_circuit_breaker(self) -> None:
        """Reset the consecutive-error counter.

        Call this between scenarios in sequential-scan mode so that a
        burst of errors in one scenario does not permanently open the circuit
        breaker and prevent all subsequent scenarios from running.
        """
        if self._consecutive_errors:
            _log.debug(
                "reset_circuit_breaker: clearing %d consecutive errors",
                self._consecutive_errors,
            )
        self._consecutive_errors = 0

    def update_default_headers(self, headers: dict[str, str] | None) -> None:
        """Merge headers into the default client headers for subsequent requests."""
        if not headers:
            return
        self._client.headers.update(headers)

    async def send(self, payload: str, session: AttackSession) -> tuple[str, list[dict]]:
        """Send a prompt payload to the target and return (response_text, tool_calls).

        Raises:
            TargetUnavailableError: after MAX_CONSECUTIVE_ERRORS consecutive 5xx
                or network errors on the chat endpoint.  4xx responses (validation
                errors, auth rejections, rate limits) do not count — the target is
                alive and responding; it simply rejected our specific payload.
        """
        data: dict | list | str = {}
        for attempt in range(self._max_429_retries + 1):
            try:
                if self._framework_adapter is not None:
                    # Framework-aware path: delegate body construction + session mgmt.
                    # Pass the AttackSession's ID as the scenario key so concurrent
                    # scenarios each maintain their own server-side ADK session.
                    scenario_key = str(session.session_id) if session.session_id else ""
                    session_id = await self._framework_adapter.ensure_session(
                        self._client, scenario_key
                    )

                    # GoogleCESAdapter manages its own HTTP transport to ces.googleapis.com.
                    # Detect it by type to avoid calling send() on mocks or other adapters.
                    try:
                        from nuguard.redteam.target.framework_adapters.google_ces import (  # noqa: PLC0415
                            GoogleCESAdapter as _GoogleCESAdapter,
                        )
                        if isinstance(self._framework_adapter, _GoogleCESAdapter):
                            _ces_text, _ces_calls = await self._framework_adapter.send(
                                self._client, payload, session_id
                            )
                            self._record_chat_success()
                            return str(_ces_text), list(_ces_calls)
                    except ImportError:
                        pass

                    body = self._framework_adapter.build_body(payload, session_id)
                    chat_path = self._framework_adapter.run_path
                else:
                    # Generic path: flat key/value body
                    value: Any = [payload] if self._chat_payload_list else payload
                    body = {self._chat_payload_key: value}
                    # Merge any previously extracted session/conversation context so the
                    # server can correlate subsequent turns within the same conversation.
                    if self._session_context:
                        body.update(self._session_context)
                    chat_path = self._chat_path

                if self._chat_payload_format == "form":
                    resp = await self._client.post(chat_path, data=body)
                else:
                    resp = await self._client.post(chat_path, json=body)
                resp.raise_for_status()
                data = resp.json()
                break
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                body_preview = exc.response.text[:300] if exc.response.text else ""
                _log.warning(
                    "Target HTTP %s  url=%s  body=%r",
                    status, exc.request.url, body_preview,
                )
                if status == 429 and attempt < self._max_429_retries:
                    delay = self._retry_delay_seconds(exc.response.headers, exc.response.text or "", attempt)
                    _log.warning(
                        "Rate limited (429) on %s — retrying in %.2fs (%d/%d)",
                        exc.request.url,
                        delay,
                        attempt + 1,
                        self._max_429_retries,
                    )
                    await asyncio.sleep(delay)
                    continue
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
                # CESAuthError requires user action — propagate rather than swallow.
                try:
                    from nuguard.common.ces_client import CESAuthError  # noqa: PLC0415
                    if isinstance(exc, CESAuthError):
                        raise
                except ImportError:
                    pass
                label = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                _log.warning(
                    "Target request failed  %s  url=%s%s",
                    label, self.base_url, self._chat_path or "",
                )
                self._record_chat_error(label[:120])
                return f"[REQUEST_ERROR: {label}]", []

        # Extract response text and tool calls.
        # When a framework adapter is present, delegate to its specialised parsers.
        tool_calls: list[dict] = []
        text = ""
        if self._framework_adapter is not None:
            text = self._framework_adapter.extract_text(data)  # type: ignore[arg-type]
            tool_calls = self._framework_adapter.extract_tool_calls(data)  # type: ignore[arg-type]
            if not text and isinstance(data, (dict, list)) and data:
                text = json.dumps(data)
            self._record_chat_success()
            return str(text), tool_calls

        # Generic extraction path — try explicit key first, then common shapes.
        # chat_response_key supports dot-notation for nested keys (e.g. "result.text").
        effective_key = self._chat_response_key or self._detected_response_key
        if effective_key and isinstance(data, dict):
            extracted = _extract_nested_key(data, effective_key)
            if isinstance(extracted, list):
                text = " ".join(str(item) for item in extracted if item is not None)
            elif extracted is not None:
                text = str(extracted)
        if not text and isinstance(data, dict):
            text = (
                data.get("response")
                or data.get("content")
                or data.get("message", {}).get("content", "")
                or ""
            )
        # Google ADK / CES format: {"outputs": [{"text": "..."}]}
        if not text and isinstance(data, dict) and isinstance(data.get("outputs"), list):
            outputs = data["outputs"]
            texts = [item.get("text", "") for item in outputs if isinstance(item, dict)]
            text = " ".join(t for t in texts if t)
        # Handle list-of-messages response (e.g. openai-cs-agents-demo)
        if not text and isinstance(data, dict) and isinstance(data.get("messages"), list):
            msgs = data["messages"]
            if msgs and isinstance(msgs[-1], dict):
                text = msgs[-1].get("content") or msgs[-1].get("text") or ""
        if not text and isinstance(data, str):
            text = data
        # Last resort: return full JSON so evaluators have something to work with.
        # Before doing so, attempt one-time auto-detection of the response key so
        # subsequent turns extract a clean text field instead of raw JSON.
        if not text and isinstance(data, dict) and data:
            if self._chat_response_key is None and self._detected_response_key is None:
                detected = self._detect_response_key(data, value if isinstance(value, str) else "")
                if detected:
                    self._detected_response_key = detected
                    extracted = _extract_nested_key(data, detected)
                    if isinstance(extracted, list):
                        text = " ".join(str(i) for i in extracted if i is not None)
                    elif extracted is not None:
                        text = str(extracted)
            if not text:
                text = json.dumps(data)

        # Extract tool calls
        raw_calls = (
            (data.get("tool_calls") if isinstance(data, dict) else None)
            or (data.get("function_calls") if isinstance(data, dict) else None)
            or (data.get("message", {}).get("tool_calls", []) if isinstance(data, dict) else [])
            or []
        )
        if isinstance(raw_calls, list):
            tool_calls = raw_calls

        # Extract and store session/conversation IDs for multi-turn forwarding.
        # Heuristic: check the top-level response JSON for common session key names.
        if isinstance(data, dict):
            for key in ("session_id", "conversation_id", "thread_id", "chat_id"):
                if key in data and data[key] is not None:
                    self._session_context[key] = data[key]

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
        for attempt in range(self._max_429_retries + 1):
            try:
                resp = await self._client.request(
                    method=method.upper(),
                    url=path,
                    json=body,
                    params=params,
                    headers=extra_headers or {},
                )
                if resp.status_code == 429 and attempt < self._max_429_retries:
                    delay = self._retry_delay_seconds(resp.headers, resp.text or "", attempt)
                    _log.warning(
                        "Rate limited (429) on %s %s — retrying in %.2fs (%d/%d)",
                        method.upper(),
                        path,
                        delay,
                        attempt + 1,
                        self._max_429_retries,
                    )
                    await asyncio.sleep(delay)
                    continue

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

        return 429, "[HTTP 429]", {}

    async def send_stream(
        self,
        payload: str,
        session: "AttackSession",
    ) -> AsyncGenerator[tuple[str, list[dict]], None]:
        """Yield ``(partial_text, tool_calls_so_far)`` chunks as the agent responds.

        For SSE endpoints (``text/event-stream`` Content-Type) the response
        body is consumed line-by-line as events arrive; each event that
        contributes text causes a new yield so callers can display partial
        output in real time.

        For non-streaming endpoints (``application/json``) the response is
        buffered as normal and yielded as a single chunk for API compatibility.

        When a framework adapter is configured, body construction and text /
        tool-call extraction are delegated to
        :meth:`~.framework_adapters.FrameworkAdapter.build_body` /
        ``extract_text`` / ``extract_tool_calls``.

        Args:
            payload: The user message to send.
            session: Current :class:`~.session.AttackSession`.

        Yields:
            ``(partial_text, tool_calls_so_far)`` tuples.  The *tool_calls*
            list is updated only when the adapter extracts them (typically on
            the final yield for SSE streams).
        """
        from nuguard.redteam.target.sse import iter_sse_events

        try:
            if self._framework_adapter is not None:
                scenario_key = str(session.session_id) if session.session_id else ""
                session_id = await self._framework_adapter.ensure_session(
                    self._client, scenario_key
                )

                # GoogleCESAdapter manages its own HTTP transport to ces.googleapis.com.
                # It has no streaming path — yield the buffered reply as a single chunk.
                try:
                    from nuguard.redteam.target.framework_adapters.google_ces import (  # noqa: PLC0415
                        GoogleCESAdapter as _GoogleCESAdapter,
                    )
                    if isinstance(self._framework_adapter, _GoogleCESAdapter):
                        _ces_text, _ces_calls = await self._framework_adapter.send(
                            self._client, payload, session_id
                        )
                        self._record_chat_success()
                        yield str(_ces_text), list(_ces_calls)
                        return
                except ImportError:
                    pass

                body = self._framework_adapter.build_body(payload, session_id)
                chat_path = self._framework_adapter.run_path
            else:
                value: Any = [payload] if self._chat_payload_list else payload
                body = {self._chat_payload_key: value}
                if self._session_context:
                    body.update(self._session_context)
                chat_path = self._chat_path

            t_start = time.monotonic()
            _stream_kwargs: dict[str, Any] = (
                {"data": body}
                if self._chat_payload_format == "form"
                else {"json": body}
            )
            async with self._client.stream("POST", chat_path, **_stream_kwargs) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")

                if "text/event-stream" in content_type:
                    # SSE path: yield incrementally
                    accumulated_text_parts: list[str] = []
                    tool_calls: list[dict] = []
                    async for event in iter_sse_events(resp):
                        if self._framework_adapter is not None:
                            # Wrap event in list so extract_text/tool_calls can
                            # handle it (ADK adapters expect a list of events)
                            chunk_text: str = self._framework_adapter.extract_text([event])
                            chunk_tools: list[dict] = self._framework_adapter.extract_tool_calls([event])
                            if chunk_tools:
                                tool_calls.extend(chunk_tools)
                        else:
                            # Generic: look for content/text in the event dict
                            chunk_text = (
                                event.get("text")
                                or event.get("content")
                                or event.get("message", "")
                                or ""
                            )
                            tool_calls = []
                        if chunk_text:
                            accumulated_text_parts.append(chunk_text)
                            yield chunk_text, list(tool_calls)
                    # If nothing yielded yet, yield what we have (empty)
                    if not accumulated_text_parts:
                        yield "", tool_calls
                    self._record_chat_success()
                else:
                    # JSON path: buffer and yield once
                    raw = await resp.aread()
                    elapsed_ms = int((time.monotonic() - t_start) * 1000)
                    _ = elapsed_ms  # available to callers via latency if needed
                    try:
                        data = json.loads(raw)
                    except Exception:
                        data = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)

                    tool_calls_json: list[dict] = []
                    if self._framework_adapter is not None:
                        text = self._framework_adapter.extract_text(data)  # type: ignore[arg-type]
                        tool_calls_json = self._framework_adapter.extract_tool_calls(data)  # type: ignore[arg-type]
                        if not text and isinstance(data, (dict, list)) and data:
                            text = json.dumps(data)
                    else:
                        text = ""
                        if isinstance(data, dict):
                            text = (
                                data.get("response")
                                or data.get("content")
                                or data.get("message", {}).get("content", "")
                                or ""
                            )
                        if not text and isinstance(data, dict) and isinstance(data.get("outputs"), list):
                            outputs = data["outputs"]
                            texts = [item.get("text", "") for item in outputs if isinstance(item, dict)]
                            text = " ".join(t for t in texts if t)
                        if not text and isinstance(data, dict) and isinstance(data.get("messages"), list):
                            msgs = data["messages"]
                            if msgs and isinstance(msgs[-1], dict):
                                text = msgs[-1].get("content") or msgs[-1].get("text") or ""
                        if not text and isinstance(data, str):
                            text = data
                        if not text and isinstance(data, dict) and data:
                            text = json.dumps(data)
                        raw_calls = (
                            (data.get("tool_calls") if isinstance(data, dict) else None)
                            or (data.get("function_calls") if isinstance(data, dict) else None)
                            or []
                        )
                        if isinstance(raw_calls, list):
                            tool_calls_json = raw_calls
                    self._record_chat_success()
                    yield str(text), tool_calls_json

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            body_preview = exc.response.text[:300] if exc.response.text else ""
            _log.warning(
                "send_stream target HTTP %s  url=%s  body=%r",
                status, exc.request.url, body_preview,
            )
            if status >= 500:
                self._record_chat_error(f"HTTP {status}")
            else:
                self._record_chat_success()
            yield f"[HTTP {status}]", []
        except Exception as exc:
            label = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            _log.warning(
                "send_stream request failed  %s  url=%s%s",
                label, self.base_url, chat_path if "chat_path" in dir() else "",
            )
            self._record_chat_error(label[:120])
            yield f"[REQUEST_ERROR: {label}]", []

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
