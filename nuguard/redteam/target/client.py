"""HTTP client for sending adversarial requests to the target application."""
from __future__ import annotations

import asyncio
import json
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
from nuguard.common.logging import get_logger
from nuguard.common.response_extraction import SESSION_ID_KEYS as _SESSION_ID_KEYS

from .session import AttackSession

if TYPE_CHECKING:
    from .framework_adapters import FrameworkAdapter

_log = get_logger(__name__)

# Dedup set: emit the ADK app_name fallback warning at most once per base URL.
_adk_fallback_warned: set[str] = set()

DEFAULT_TIMEOUT = 30.0

# Well-known OpenAI/Anthropic/LangChain-style chat-history field names.  When
# chat_payload_key matches one of these (and chat_payload_list is True), the
# outgoing value is shaped as a replayed [{role, content}, ...] message list
# instead of a bare [prompt] list — see TargetAppClient._build_chat_payload_value.
_MESSAGE_HISTORY_KEYS: frozenset[str] = frozenset(
    {"messages", "history", "conversation", "chat_history"}
)


def _is_message_history_key(key: str) -> bool:
    return key.strip().lower() in _MESSAGE_HISTORY_KEYS


# Path-parameter placeholder styles seen across frameworks: FastAPI/ASP.NET
# Core use `{id}`, NestJS/Express use `:id`. Substituted against
# TargetAppClient._path_param_values before every request — see
# set_path_param() and tests/apps/studyield-app/2-step-chat.md (the "two-step
# chat" plan this implements: bootstrap a resource, e.g. POST
# /chat/conversations, then substitute its id into the chat path template).
_COLON_PATH_PARAM_RE = re.compile(r":(\w+)")
_BRACE_PATH_PARAM_RE = re.compile(r"\{(\w+)\}")


def _substitute_path_params(path: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """Substitute ``:name``/``{name}`` placeholders in *path* from *values*.

    Returns ``(resolved_path, missing_names)`` — *missing_names* lists any
    placeholder with no bound value (left untouched in the returned path so
    the caller can surface a clear config error instead of sending the
    literal placeholder to the target).
    """
    missing: list[str] = []

    def _repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in values:
            return values[name]
        missing.append(name)
        return m.group(0)

    resolved = _COLON_PATH_PARAM_RE.sub(_repl, path)
    resolved = _BRACE_PATH_PARAM_RE.sub(_repl, resolved)
    return resolved, missing


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
        chat_payload_extras: dict[str, Any] | None = None,
        max_concurrent_requests: int = 0,
        max_transient_hold_seconds: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._chat_path = chat_path
        self._chat_payload_key = chat_payload_key
        self._chat_payload_list = chat_payload_list
        self._chat_payload_format = (
            chat_payload_format if chat_payload_format in ("json", "form") else "json"
        )
        self._chat_payload_extras: dict[str, Any] = chat_payload_extras or {}
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
        # Two-step chat bootstrap: values bound via set_path_param() to
        # substitute :name/{name} placeholders in _chat_path before each
        # request (e.g. a conversation id created by a prerequisite POST) —
        # see tests/apps/studyield-app/2-step-chat.md.
        self._path_param_values: dict[str, str] = {}
        # Global HTTP semaphore: limits concurrent in-flight requests across ALL
        # callers sharing this client instance.  Useful when the target app has a
        # low Azure OpenAI / LLM concurrency quota and returns transient errors
        # ("having difficulty connecting") when multiple requests arrive
        # simultaneously.  0 = unlimited (default behaviour).
        self._request_sem: asyncio.Semaphore | None = (
            asyncio.Semaphore(max_concurrent_requests)
            if max_concurrent_requests > 0
            else None
        )
        # Max total seconds to hold the semaphore during transient-error retries
        # on the first request of a new chain.  After this limit the semaphore is
        # released so other chains can proceed rather than blocking for the full
        # objective timeout.
        self._max_transient_hold_seconds: float = max(0.0, max_transient_hold_seconds)
        # Optional framework-specific adapter (e.g. Google ADK).  When set,
        # send() delegates body construction and response parsing to the adapter.
        self._framework_adapter: FrameworkAdapter | None = framework_adapter
        # Populated by build_target_app_client() with human-readable notes about
        # automatic config resolution (URL fallback, auth upgrade, etc.).
        self.resolution_notes: list[str] = []

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

    @property
    def chat_path(self) -> str:
        """The currently configured chat endpoint path."""
        return self._chat_path

    def set_chat_endpoint(
        self,
        path: str,
        payload_key: str,
        payload_list: bool = False,
        response_key: str | None = None,
    ) -> None:
        """Replace the configured chat endpoint without rebuilding the client.

        Used for endpoint rotation when the primary endpoint returns 405/404.
        Resets the circuit breaker so the new path starts with a clean slate.
        """
        _log.info(
            "set_chat_endpoint: rotating from %s → %s (key=%s)",
            self._chat_path, path, payload_key,
        )
        self._chat_path = path
        self._chat_payload_key = payload_key
        self._chat_payload_list = payload_list
        if response_key is not None:
            self._chat_response_key = response_key
        # Clear auto-detected response key so it is re-detected for the new endpoint
        self._detected_response_key = None
        # A path-param binding from the previous endpoint (e.g. {"id": "..."})
        # is not guaranteed to apply to the new one even if the param name
        # matches — force a fresh bootstrap for whatever this endpoint needs.
        self._path_param_values = {}
        self.reset_circuit_breaker()

    def set_path_param(self, name: str, value: str) -> None:
        """Bind *value* for a ``:name``/``{name}`` placeholder in the chat path.

        Used by the two-step (resource-bootstrap) chat flow: a prerequisite
        request (e.g. ``POST /chat/conversations``) creates a resource whose
        id must be substituted into the chat endpoint's path template (e.g.
        ``POST /chat/conversations/:id/messages``) before any turn can be
        sent. See tests/apps/studyield-app/2-step-chat.md.
        """
        self._path_param_values[name] = value

    def update_default_headers(self, headers: dict[str, str] | None) -> None:
        """Merge headers into the default client headers for subsequent requests."""
        if not headers:
            return
        self._client.headers.update(headers)

    async def send(self, payload: str, session: AttackSession) -> tuple[str, list[dict]]:
        """Send a prompt payload to the target and return (response_text, tool_calls).

        When ``max_concurrent_requests`` is set (semaphore mode), transient app
        errors ("having difficulty connecting") are retried **inside** the semaphore
        scope so no other concurrent chain can send while the target is recovering.
        This prevents the thundering-herd pattern where multiple chains hammer a
        cold-starting Azure Container App during the retry window.

        Raises:
            TargetUnavailableError: after MAX_CONSECUTIVE_ERRORS consecutive 5xx
                or network errors on the chat endpoint.  4xx responses (validation
                errors, auth rejections, rate limits) do not count — the target is
                alive and responding; it simply rejected our specific payload.
        """
        if self._request_sem is not None:
            async with self._request_sem:
                return await self._send_with_transient_retry(payload, session)
        return await self._send_impl(payload, session)

    async def _send_with_transient_retry(
        self, payload: str, session: AttackSession
    ) -> tuple[str, list[dict]]:
        """Send with in-semaphore transient-error retries (holds the semaphore during waits).

        Retries :data:`~nuguard.common.transport.RETRIABLE_OUTCOMES` (app-transient
        phrases AND HTTP 502/503/504 gateway errors) with capped backoff, all while
        holding the semaphore.  Retrying stops when:

        * The target returns a non-retriable response → ``return``.
        * ``_max_transient_hold_seconds`` of total wait time has elapsed → releases
          the semaphore so other chains can proceed (prevents one chain from blocking
          all others for the full objective timeout when the cause is systemic, e.g.
          shared Azure OpenAI quota contention between nuguard and the target app).
        * The objective timeout fires (``asyncio.CancelledError``) → re-raised so the
          scheduler records the objective as ``"timeout"``.

        The backoff schedule is :data:`~nuguard.common.rate_limit.TRANSIENT_ERROR_RETRY_DELAYS`
        for the first N attempts, then the last (largest) delay caps all subsequent waits.
        """
        from nuguard.common.rate_limit import (  # noqa: PLC0415
            GATEWAY_ERROR_RETRY_DELAYS,
            TRANSIENT_ERROR_RETRY_DELAYS,
        )
        from nuguard.common.transport import (  # noqa: PLC0415
            RETRIABLE_OUTCOMES,
            TransportOutcome,
            classify_transport,
        )

        attempt = 0
        total_waited = 0.0
        text: str = ""
        calls: list[dict] = []

        while True:
            text, calls = await self._send_impl(payload, session)
            outcome = classify_transport(text)
            if outcome not in RETRIABLE_OUTCOMES:
                return text, calls

            # Choose the retry schedule based on the error type:
            # - HTTP_GATEWAY_ERROR (502/503/504): short delays (2 s, 5 s) for Azure quota
            #   blips that typically resolve in under 3 seconds.
            # - APP_TRANSIENT (cold-start friendly text): long delays (60 s, 120 s) to
            #   allow Azure Container Apps (minReplicas=0) time to spin up.
            is_gateway = outcome == TransportOutcome.HTTP_GATEWAY_ERROR
            delays = list(GATEWAY_ERROR_RETRY_DELAYS if is_gateway else TRANSIENT_ERROR_RETRY_DELAYS)
            cap_delay = delays[-1] if delays else 5.0

            # For gateway errors: enforce a hard retry cap (len(delays)) so we don’t
            # hold the semaphore indefinitely on a persistently broken endpoint.
            # For app-transient errors: the existing max_transient_hold_seconds gate
            # applies (cold-start can take minutes).
            if is_gateway and attempt >= len(delays):
                _log.warning(
                    "HTTP gateway error persisted after %d retry attempt(s) — "
                    "incrementing circuit breaker and releasing.",
                    attempt,
                )
                self._record_chat_error(f"HTTP gateway (after {attempt} retries)")
                return text, calls

            # If the session already has prior turns, the target backend is up
            # and responding — the transient is either a quota-induced blip or a
            # content-filter block on this specific adversarial payload.  Allow
            # one retry (attempt 0 → 1) so a momentary quota spike can recover;
            # after that, stop retrying since the same payload will keep being
            # blocked by the content filter.
            if session.turns and attempt >= 1:
                _log.debug(
                    "Transient response on turn %d (retry #%d) — treating as content-filter "
                    "block (backend is healthy); stopping retry.",
                    len(session.turns) + 1,
                    attempt,
                )
                if is_gateway:
                    self._record_chat_error(f"HTTP gateway (turn {len(session.turns) + 1})")
                return text, calls

            # Release the semaphore after holding it for too long.  Systemic issues
            # (e.g. shared Azure OpenAI quota contention) will not resolve with more
            # retries from this chain; other chains deserve a chance to make progress.
            if self._max_transient_hold_seconds > 0 and total_waited >= self._max_transient_hold_seconds:
                _log.warning(
                    "Transient errors persisted after %.0fs of in-semaphore retries "
                    "(max_transient_hold=%.0fs) — releasing semaphore so other chains "
                    "can proceed. Last response was retriable but target did not recover.",
                    total_waited, self._max_transient_hold_seconds,
                )
                if is_gateway:
                    self._record_chat_error(f"HTTP gateway (transient hold {total_waited:.0f}s)")
                return text, calls

            delay = delays[attempt] if attempt < len(delays) else cap_delay
            attempt += 1
            _log.info(
                "Retriable transport error — waiting %.0fs before retry #%d "
                "[semaphore held; no other chains will send during this window]",
                delay, attempt,
            )
            try:
                await asyncio.sleep(delay)
                total_waited += delay
            except asyncio.CancelledError:
                # Objective timeout fired during the sleep — release the semaphore
                # and propagate so the scheduler records this as "timeout".
                _log.warning(
                    "In-semaphore transient retry cancelled (objective timeout) "
                    "after %d attempt(s); target may still be recovering.",
                    attempt,
                )
                raise

    def _build_chat_payload_value(self, payload: str, session: AttackSession) -> Any:
        """Shape the outgoing value for ``chat_payload_key`` in the generic (non-adapter) path.

        Three shapes, chosen by ``chat_payload_list``/``chat_payload_key``:

        - Flat string (``chat_payload_list=False``): the raw prompt text.
        - Bare list (``chat_payload_list=True``, key not a known message-history
          name): ``[prompt]`` — existing behaviour, e.g. LangGraph's
          ``phrases=[...]``.
        - OpenAI-style message history (``chat_payload_list=True`` and key is
          one of ``messages``/``history``/``conversation``/``chat_history``):
          replay prior turns from *session* as alternating ``{role, content}``
          dicts, then append the current turn. This is the standard shape used
          by OpenAI/Anthropic/LangChain-style chat APIs; endpoints whose only
          accepted body shape is ``messages=[...]`` reject a bare string or a
          bare-string list outright (400/422), aborting every scenario.
        """
        if not self._chat_payload_list:
            return payload
        if not _is_message_history_key(self._chat_payload_key):
            return [payload]
        history: list[dict[str, str]] = []
        for turn in session.turns:
            history.append({"role": "user", "content": turn.prompt})
            if turn.response:
                history.append({"role": "assistant", "content": turn.response})
        history.append({"role": "user", "content": payload})
        return history

    async def _send_impl(self, payload: str, session: AttackSession) -> tuple[str, list[dict]]:
        """Inner send implementation (called with or without the request semaphore)."""
        data: dict | list | str = {}
        for attempt in range(self._max_429_retries + 1):
            try:
                session_id: str = ""
                if self._framework_adapter is not None:
                    # Framework-aware path: delegate body construction + session mgmt.
                    # Pass the AttackSession's ID as the scenario key so concurrent
                    # scenarios each maintain their own server-side ADK session.
                    scenario_key = str(session.session_id) if session.session_id else ""
                    try:
                        session_id = await self._framework_adapter.ensure_session(
                            self._client, scenario_key
                        )
                    except RuntimeError as _adk_err:
                        # When the target does not expose /list-apps (e.g. it wraps ADK
                        # behind a custom REST endpoint), silently drop the ADK adapter
                        # and fall through to the generic HTTP POST path below.
                        if "app_name could not be determined" in str(_adk_err):
                            _base = self.base_url or ""
                            if _base not in _adk_fallback_warned:
                                _adk_fallback_warned.add(_base)
                                _log.warning(
                                    "ADK adapter could not resolve app_name "
                                    "(target does not expose GET /list-apps and "
                                    "the SBOM contains no adk_app_name). "
                                    "Falling back to generic HTTP POST — "
                                    "multi-turn session fidelity may be reduced. "
                                    "Fix: run 'nuguard sbom generate' against the source "
                                    "so app_name is captured from Runner(app_name=...), "
                                    "or set 'adk.app_name: <name>' in nuguard.yaml."
                                )
                            self._framework_adapter = None
                        else:
                            raise

                if self._framework_adapter is not None:
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
                    value: Any = self._build_chat_payload_value(payload, session)
                    body = {self._chat_payload_key: value}
                    # Merge any previously extracted session/conversation context so the
                    # server can correlate subsequent turns within the same conversation.
                    if self._session_context:
                        body.update(self._session_context)
                    # Merge static extra fields (e.g. vehicleState, language) declared in
                    # chat_payload_extras — the message key always takes precedence.
                    if self._chat_payload_extras:
                        body = {**self._chat_payload_extras, **body}
                    chat_path, _missing_params = _substitute_path_params(
                        self._chat_path, self._path_param_values
                    )
                    if _missing_params:
                        _log.warning(
                            "_send_impl: unresolved path param(s) %s in chat path template %r",
                            _missing_params, self._chat_path,
                        )
                        return f"[CONFIG_ERROR: unresolved path param {_missing_params[0]!r}]", []

                if self._chat_payload_format == "form":
                    resp = await self._client.post(chat_path, data=body)
                else:
                    resp = await self._client.post(chat_path, json=body)
                resp.raise_for_status()
                _content_type = resp.headers.get("content-type", "")
                if "text/event-stream" in _content_type and self._framework_adapter is None:
                    # Non-streaming send() against an SSE-only chat endpoint (e.g.
                    # a FastAPI StreamingResponse) — resp.json() would raise
                    # JSONDecodeError on the buffered "data: {...}\n\n" body,
                    # which _record_chat_error would then wrongly count toward
                    # the circuit breaker as if the target were unreachable.
                    # Parse the buffered SSE events and join their text chunks;
                    # the isinstance(data, str) fallback below picks this up.
                    from nuguard.redteam.target.sse import parse_sse_events  # noqa: PLC0415

                    _sse_events = parse_sse_events(resp.text)
                    _sse_text = "".join(
                        str(_ev.get("text") or _ev.get("content") or _ev.get("message") or "")
                        for _ev in _sse_events
                    )
                    data = _sse_text or json.dumps(_sse_events)
                else:
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
                #
                # 502/503/504 gateway errors are deferred: _send_with_transient_retry
                # will retry with short backoff and only increment the circuit breaker
                # after all retries are exhausted.  This prevents a single transient
                # Azure OpenAI quota spike from inflating the shared error counter.
                # That deferral only applies when a semaphore is configured — that's
                # the only case send() actually routes through _send_with_transient_retry
                # (see send() above). Without a semaphore, _send_impl is called directly
                # and nothing else will ever record the error, so it must count here.
                if status in (502, 503, 504) and self._request_sem is not None:
                    pass  # caller handles circuit-breaker accounting after retries
                elif status >= 500:
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
                or data.get("text")
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
        if isinstance(data, dict):
            for key in _SESSION_ID_KEYS:
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
                value: Any = self._build_chat_payload_value(payload, session)
                body = {self._chat_payload_key: value}
                if self._session_context:
                    body.update(self._session_context)
                if self._chat_payload_extras:
                    body = {**self._chat_payload_extras, **body}
                chat_path, _missing_params = _substitute_path_params(
                    self._chat_path, self._path_param_values
                )
                if _missing_params:
                    _log.warning(
                        "send_stream: unresolved path param(s) %s in chat path template %r",
                        _missing_params, self._chat_path,
                    )
                    yield f"[CONFIG_ERROR: unresolved path param {_missing_params[0]!r}]", []
                    return

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
                                or data.get("text")
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
