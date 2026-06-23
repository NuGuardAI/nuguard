"""Transport-layer outcome classification for redteam HTTP responses.

:func:`classify_transport` is the single source of truth for deciding what a
response string from :class:`~nuguard.redteam.target.client.TargetAppClient`
means at the network/transport level.  All higher-level code (executors,
schedulers, runner) should call this function rather than inspecting prefix
strings or phrase lists directly.
"""
from __future__ import annotations

import json
from enum import Enum

# ---------------------------------------------------------------------------
# App-level transient error phrases
# ---------------------------------------------------------------------------
# The target application's own backend returned a transient
# connection/service-unavailable error as a 200-OK chat response (e.g. an
# orchestrator catching an MCP cold-start exception and returning a friendly
# fallback message).  These are NOT agent refusals — they indicate the target
# is temporarily unavailable.
#
# Re-exported by nuguard.redteam.llm_engine.refusal_patterns for backward compat.
APP_TRANSIENT_ERROR_PATTERNS: frozenset[str] = frozenset({
    "having difficulty connecting",
    "please try again in a moment",
    "service is temporarily unavailable",
    "unable to process your request at this time",
    "i'm having trouble connecting",
    "temporarily unable to respond",
})

# JSON body keys/values that indicate a structurally expressed transient error.
# Checked when the response body is valid JSON (handles non-English backends).
_JSON_TRANSIENT_KEYS: frozenset[str] = frozenset({
    "temporarily_unavailable", "service_unavailable", "unavailable",
})
_JSON_TRANSIENT_CODES: frozenset[str] = frozenset({
    "SERVICE_UNAVAILABLE", "TEMPORARILY_UNAVAILABLE", "GATEWAY_ERROR",
})


class TransportOutcome(str, Enum):
    """Classification of a transport-level response from the target app."""

    OK = "ok"
    HTTP_4XX = "http_4xx"                    # 400–499 excl. 429 — config/auth issue
    HTTP_5XX = "http_5xx"                    # 500 — app bug / internal failure
    HTTP_GATEWAY_ERROR = "http_gateway_error"  # 502/503/504 — temporarily unavailable
    REQUEST_ERROR = "request_error"          # network / DNS / connectivity failure
    RATE_LIMIT = "rate_limit"                # 429 exhausted all per-request retries
    APP_TRANSIENT = "app_transient"          # backend cold-start friendly error text


# Outcomes that should be retried with in-semaphore backoff.
# 502/503/504 behave like app-transient errors: the backend is alive but
# temporarily overloaded or being restarted.
RETRIABLE_OUTCOMES: frozenset[TransportOutcome] = frozenset({
    TransportOutcome.APP_TRANSIENT,
    TransportOutcome.HTTP_GATEWAY_ERROR,
})


def classify_transport(response: str) -> TransportOutcome:
    """Classify a TargetAppClient response string into a :class:`TransportOutcome`.

    Classification order (first match wins):

    1. ``[HTTP 429...]`` → :attr:`~TransportOutcome.RATE_LIMIT`
    2. ``[HTTP 5...]`` → :attr:`~TransportOutcome.HTTP_GATEWAY_ERROR` for
       502/503/504, :attr:`~TransportOutcome.HTTP_5XX` for 500 / other 5xx
    3. ``[HTTP 4...]`` → :attr:`~TransportOutcome.HTTP_4XX`
    4. ``[REQUEST_ERROR:...]`` → :attr:`~TransportOutcome.REQUEST_ERROR`
    5. JSON body with structured error signals → :attr:`~TransportOutcome.APP_TRANSIENT`
    6. Phrase match against :data:`APP_TRANSIENT_ERROR_PATTERNS` → :attr:`~TransportOutcome.APP_TRANSIENT`
    7. Default → :attr:`~TransportOutcome.OK`
    """
    # --- Sentinel-prefix responses from TargetAppClient ---
    if response.startswith("[HTTP 429"):
        return TransportOutcome.RATE_LIMIT

    if response.startswith("[HTTP 5"):
        # Extract the numeric code from "[HTTP 5xx] ..." — "[HTTP " is 6 chars.
        try:
            code = int(response[6:9])
        except ValueError:
            code = 500
        if code in (502, 503, 504):
            return TransportOutcome.HTTP_GATEWAY_ERROR
        return TransportOutcome.HTTP_5XX

    if response.startswith("[HTTP 4"):
        return TransportOutcome.HTTP_4XX

    if response.startswith("[REQUEST_ERROR:"):
        return TransportOutcome.REQUEST_ERROR

    # --- Structured JSON error body (non-English backends) ---
    stripped = response.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict) and _is_json_transient(data):
                return TransportOutcome.APP_TRANSIENT
        except (json.JSONDecodeError, ValueError):
            pass

    # --- English phrase matching (fallback for plain-text chat responses) ---
    resp_lower = response.lower()
    if any(pat in resp_lower for pat in APP_TRANSIENT_ERROR_PATTERNS):
        return TransportOutcome.APP_TRANSIENT

    return TransportOutcome.OK


def _is_json_transient(data: dict) -> bool:
    """Return True when a JSON body structurally signals a transient error."""
    # {"retryable": true}
    if data.get("retryable") is True:
        return True
    # {"error": "temporarily_unavailable"} or {"error": {"code": "SERVICE_UNAVAILABLE"}}
    error = data.get("error", "")
    if isinstance(error, str) and error.lower() in _JSON_TRANSIENT_KEYS:
        return True
    if isinstance(error, dict):
        code = str(error.get("code", "")).upper()
        if code in _JSON_TRANSIENT_CODES:
            return True
    # {"status": "unavailable"} / {"code": "SERVICE_UNAVAILABLE"}
    status = str(data.get("status", "")).lower()
    if status in _JSON_TRANSIENT_KEYS:
        return True
    code_val = str(data.get("code", "")).upper()
    if code_val in _JSON_TRANSIENT_CODES:
        return True
    return False
