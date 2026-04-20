"""Live HTTP probing to discover which SBOM endpoint accepts chat requests.

When ``target_endpoint`` is not explicitly configured, :func:`probe_chat_endpoints`
cycles through all POST ``API_ENDPOINT`` nodes in the SBOM, sends a lightweight
test payload to each, and returns the first path that yields a chat-like response.

A "chat-like" response is defined as:
  - HTTP 2xx with a non-empty JSON body containing at least one string value, OR
  - HTTP 4xx that is *not* 404/405 (endpoint exists but rejected our payload shape)

Endpoints that match exclusion patterns (health, login, auth, metrics, static,
docs, callbacks) are skipped regardless of HTTP status.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument

_log = logging.getLogger(__name__)

# Endpoint path fragments that are definitively NOT chat endpoints.
# Keep this list tight — false exclusions are worse than false inclusions
# because the probe will verify via HTTP anyway.
_EXCLUDE_PATTERNS = re.compile(
    r"/(health|ping|ready|live|metrics|auth|logout|token|oauth|callback"
    r"|webhook|static|assets|docs|openapi|swagger|favicon|vite"
    r"|upload|download)(/|$)",
    re.IGNORECASE,
)

# Payload shapes to attempt when we don't know the key.  Tried in order.
_PROBE_PAYLOADS: list[tuple[str, bool]] = [
    ("message", False),
    ("phrases", True),
    ("input", False),
    ("query", False),
    ("prompt", False),
    ("text", False),
    ("content", False),
    ("msg", False),
]

_TEST_MESSAGE = "Hello, this is a connectivity test."


def _sbom_post_paths(sbom: "AiSbomDocument") -> list[str]:
    """Return POST endpoint paths from the SBOM, scored by chat-likelihood."""
    from nuguard.sbom.models import NodeType  # noqa: PLC0415

    scored: list[tuple[int, str]] = []
    for node in sbom.nodes:
        if node.component_type != NodeType.API_ENDPOINT:
            continue
        meta = node.metadata
        if not meta:
            continue
        path: str = (meta.endpoint or "").strip()
        if not path or not path.startswith("/"):
            continue
        # Skip parameterised paths like /user/{id}
        if "{" in path:
            continue
        # Skip non-POST
        if meta.method and meta.method.upper() not in ("POST", ""):
            continue

        if _EXCLUDE_PATTERNS.search(path):
            continue

        path_l = path.lower()
        score = 0
        for token in ("langgraph", "run", "chat", "message", "query", "complete",
                       "infer", "generate", "respond", "agent", "converse", "talk",
                       "assistant", "llm", "ai"):
            if token in path_l:
                score += 2
                break
        if meta.chat_payload_key:
            score += 3
        if node.confidence >= 0.9:
            score += 1

        scored.append((score, path))

    # Sort descending by score, then alphabetically for determinism
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [p for _, p in scored]


async def probe_chat_endpoints(
    target_url: str,
    sbom: "AiSbomDocument",
    auth_headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    known_payload_key: str | None = None,
    known_payload_list: bool = False,
    known_response_key: str | None = None,
) -> tuple[str, str, bool] | None:
    """Probe SBOM POST endpoints and return the first chat-capable one.

    Returns ``(path, payload_key, payload_list)`` for the winning endpoint, or
    ``None`` if no endpoint responded usefully.

    Args:
        target_url: Base URL of the target app (scheme + host).
        sbom: Parsed AI-SBOM document containing API_ENDPOINT nodes.
        auth_headers: Optional auth headers forwarded on each probe request.
        timeout: Per-request timeout in seconds.
        known_payload_key: If the caller already knows the payload key, only
            that shape is tried (skips the multi-shape probe loop).
        known_payload_list: Whether the known payload key wraps value in a list.
        known_response_key: If set, presence of this key in the response JSON
            signals success even if the value is empty.
    """
    paths = _sbom_post_paths(sbom)
    # ── ADK fast-path ────────────────────────────────────────────────────────
    # When the SBOM identifies the target as a Google ADK application we skip
    # the generic multi-shape probe entirely.  ADK uses a fixed protocol
    # (RunAgentRequest) that bears no resemblance to the flat key/value shapes
    # in _PROBE_PAYLOADS; trying them would always produce HTTP 422, consuming
    # probe time and polluting the logs.
    # Instead, do a quick health check to confirm the server is reachable and
    # return the well-known '/run' path immediately.
    from nuguard.redteam.target.framework_adapters.google_adk import (  # noqa: PLC0415
        ADK_FRAMEWORK_NAMES,
    )
    summary = getattr(sbom, "summary", None)
    sbom_frameworks: list[str] = []
    if summary is not None:
        raw_frameworks = getattr(summary, "frameworks", None)
        if isinstance(raw_frameworks, (list, tuple)):
            sbom_frameworks = [str(f).lower() for f in raw_frameworks if f]
    if ADK_FRAMEWORK_NAMES & set(sbom_frameworks):
        adk_run_path = "/run"
        _log.info(
            "endpoint_probe: Google ADK detected in SBOM — skipping generic probe, using %s",
            adk_run_path,
        )
        return adk_run_path, "__adk__", False

    # ── Generic probe loop ────────────────────────────────────────────────────
    # Always append common fallback paths (deduplicated) so we try them even
    # when the SBOM has no useful metadata.
    for fallback in ("/chat", "/run", "/api/chat", "/v1/chat", "/query", "/agent"):
        if fallback not in paths:
            paths.append(fallback)
    if not paths:
        _log.debug("endpoint_probe: no probe-eligible paths")
        return None

    base = target_url.rstrip("/")
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "User-Agent": "nuguard-probe/1.0",
    }
    if auth_headers:
        headers.update(auth_headers)

    payload_shapes = (
        [(known_payload_key, known_payload_list)]
        if known_payload_key
        else _PROBE_PAYLOADS
    )

    async with httpx.AsyncClient(
        base_url=base,
        timeout=httpx.Timeout(timeout),
        headers=headers,
        follow_redirects=True,
    ) as client:
        for path in paths:
            _log.info("endpoint_probe: trying %s%s", base, path)
            for pay_key, pay_list in payload_shapes:
                value: object = [_TEST_MESSAGE] if pay_list else _TEST_MESSAGE
                body = {pay_key: value}
                try:
                    resp = await client.post(path, content=json.dumps(body))
                except Exception as exc:
                    _log.debug("endpoint_probe: %s — request error: %s", path, exc)
                    break  # network error; skip remaining shapes for this path

                status = resp.status_code
                if status in (404, 405):
                    _log.debug(
                        "endpoint_probe: %s — %d (not found/method not allowed)", path, status
                    )
                    break  # try next path

                if status >= 500:
                    _log.debug("endpoint_probe: %s — %d server error", path, status)
                    break  # server error; skip this path

                # 2xx or 4xx (other than 404/405) — endpoint exists
                if status < 300:
                    # Verify the response looks like a chat reply
                    try:
                        data = resp.json()
                    except Exception:
                        data = {}
                    if _looks_like_chat_response(data, known_response_key):
                        _log.info(
                            "endpoint_probe: selected %s (payload_key=%r, list=%s, status=%d)",
                            path, pay_key, pay_list, status,
                        )
                        return path, pay_key, pay_list
                    # Response exists but doesn't look like chat — try next shape
                    _log.debug(
                        "endpoint_probe: %s key=%r → status %d but body doesn't look like chat",
                        path, pay_key, status,
                    )
                    continue

                # 4xx other than 404/405 — endpoint exists, payload shape may be wrong
                _log.debug(
                    "endpoint_probe: %s key=%r → %d (endpoint exists, trying next shape)",
                    path, pay_key, status,
                )
                # If known_payload_key was provided but got 4xx, still accept it
                # (endpoint is reachable; payload mismatch is config, not discovery)
                if known_payload_key:
                    _log.info(
                        "endpoint_probe: selected %s (payload_key=%r known, status=%d)",
                        path, pay_key, status,
                    )
                    return path, pay_key, pay_list

    _log.warning(
        "endpoint_probe: no chat-capable endpoint found after probing %d paths", len(paths)
    )
    return None


def _looks_like_chat_response(data: object, response_key: str | None = None) -> bool:
    """Return True if *data* looks like a processed response from a chat endpoint.

    For probe purposes the bar is intentionally low: any non-empty JSON object
    that is not a plain error envelope counts.  We are discovering *which*
    endpoint handles chat requests, not validating response quality.
    """
    if not isinstance(data, dict) or not data:
        return False
    # Explicit key wins immediately
    if response_key and response_key in data:
        return True
    # Generic error envelopes — skip these
    error_only = set(data.keys()) <= {"error", "detail", "message", "code", "status"}
    if error_only and len(data) <= 2:
        return False
    # Any response with ≥2 keys is treated as a real API response, not an error
    if len(data) >= 2:
        return True
    # Single-key response: accept if it contains a known chat-y key
    for key in ("response", "content", "prognosis", "text", "output",
                "answer", "result", "reply", "choices", "messages"):
        if key in data:
            return True
    return False
