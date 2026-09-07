"""Per-endpoint liveness/reachability marking, shared by ``behavior`` and ``redteam``.

:func:`~nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint`
only pings the single, primary chat endpoint before scenarios run. Every
other SBOM-discovered ``API_ENDPOINT`` node is never individually checked, so
a dead/misresolved REST path (stale route, guessed slug) silently reads
identically in a report to a live endpoint that actually defended against an
attack — the attack surface was never reached at all.

:func:`check_endpoint_liveness` sends one lightweight authenticated request to
every attackable ``API_ENDPOINT`` node and records ``operational`` (``True``/
``False``/``None``) directly on the node's metadata, so both scenario
generators (see :mod:`nuguard.common.endpoint_scenario_gate`) can skip
direct-HTTP scenario synthesis for confirmed-dead endpoints, the same way
they already skip structurally-invalid ones.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

from nuguard.common.endpoint_preflight import _ROTATION_TRIGGER_PREFIXES, _TEST_MESSAGE
from nuguard.common.logging import get_logger
from nuguard.common.response_extraction import build_minimal_payload

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument, NodeMetadata

_log = get_logger(__name__)

DEFAULT_LIVENESS_CACHE_TTL_SECONDS = 3600.0

# Mirrors nuguard.redteam.scenarios.generator.ScenarioGenerator's
# _NON_REST_ENDPOINT_RE / _looks_like_rest_path — duplicated (not imported)
# because nuguard.common is a lower layer than nuguard.redteam and must not
# import from it (same constraint documented in endpoint_preflight.py).
_NON_REST_ENDPOINT_RE = re.compile(
    r"^[\w.\-]+:\d+|^[a-z][a-z0-9+.\-]*://|\(\s*[a-z]+\s*\)\s*$", re.IGNORECASE
)


def _looks_like_rest_path(endpoint: str | None) -> bool:
    """True when *endpoint* is an attackable HTTP path, not a bind
    address / MCP-SSE annotation / other non-REST identifier."""
    if not endpoint or not endpoint.strip():
        return False
    candidate = endpoint.strip()
    if not candidate.startswith("/"):
        return False
    return not _NON_REST_ENDPOINT_RE.search(candidate)


# HTTP status codes that mean "path exists, we're just not authorized" —
# the endpoint is reachable and correctly gated, not dead. Mirrors the
# 401/403-not-404 distinction in nuguard.redteam.executor.orchestrator's
# _maybe_mark_endpoint_not_found (only a 404-only run is reclassified as
# "not reached"; a 401/403 anywhere counts as a real, defended attempt).
_AUTH_ENFORCED_STATUS_CODES = frozenset({401, 403})

# Status codes considered a rotation-trigger / "endpoint is wrong" signal,
# as integers (mirrors endpoint_preflight._ROTATION_TRIGGER_PREFIXES, which
# is expressed as "[HTTP NNN]" string prefixes over chat-path responses).
_DEAD_STATUS_CODES = frozenset({400, 404, 405, 422})


class LivenessReport(BaseModel):
    """Aggregate result of a :func:`check_endpoint_liveness` pass."""

    checked: int = 0
    operational: int = 0
    non_operational: int = 0
    skipped: int = 0
    cached: int = 0
    notes: list[str] = Field(default_factory=list)


class _InvokeClient(Protocol):
    async def invoke_endpoint(
        self,
        path: str,
        method: str = "POST",
        body: dict | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        strip_auth: bool = False,
    ) -> tuple[int, str, dict]: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_status(status_code: int, response_text: str) -> tuple[bool, str]:
    """Return (operational, note) for a completed (non-exception) request."""
    if status_code in _AUTH_ENFORCED_STATUS_CODES:
        return True, f"HTTP {status_code} — endpoint reachable, auth correctly enforced."
    if status_code in _DEAD_STATUS_CODES or response_text.startswith(_ROTATION_TRIGGER_PREFIXES):
        return False, f"HTTP {status_code} — rotation-trigger status, endpoint likely wrong/dead."
    if status_code == 0 or response_text.startswith("[REQUEST_ERROR"):
        return False, f"[NETWORK] {response_text}"
    return True, f"HTTP {status_code} — endpoint reachable."


async def _ping_one(
    client: "_InvokeClient",
    meta: "NodeMetadata",
    *,
    per_endpoint_timeout: float,
) -> tuple[bool | None, str]:
    """Ping a single endpoint; returns (operational, note). Never raises."""
    endpoint = meta.endpoint or ""
    method = (meta.method or "GET").upper()
    is_chat_like = bool(meta.accepts_user_input) or bool(getattr(meta, "chat_payload_key", None))

    body: dict | None = None
    if method in ("POST", "PUT", "PATCH"):
        if is_chat_like:
            chat_key = getattr(meta, "chat_payload_key", None) or "message"
            body = {chat_key: _TEST_MESSAGE}
        else:
            schema = getattr(meta, "request_body_schema", None) or {}
            body = build_minimal_payload(schema) if schema else {}

    try:
        status_code, response_text, _json = await asyncio.wait_for(
            client.invoke_endpoint(endpoint, method=method, body=body),
            timeout=per_endpoint_timeout,
        )
    except asyncio.TimeoutError:
        note = f"[TIMEOUT] no response within {per_endpoint_timeout}s"
        _log.warning("Liveness: %s %s timed out after %.1fs", method, endpoint, per_endpoint_timeout)
        return False, note
    except Exception as exc:
        note = f"[NETWORK] {type(exc).__name__}: {exc}"
        _log.error("Liveness: %s %s raised: %s", method, endpoint, exc)
        return False, note

    operational, note = _classify_status(status_code, response_text)
    if operational:
        _log.debug("Liveness: %s %s -> operational (%s)", method, endpoint, note)
    else:
        _log.warning("Liveness: %s %s -> non-operational (%s)", method, endpoint, note)
    return operational, note


def _cached_liveness_is_fresh(meta: "NodeMetadata", ttl_seconds: float) -> bool:
    """True when *meta* carries a liveness result probed within *ttl_seconds*."""
    if meta.operational is None or not meta.liveness_checked_at:
        return False
    try:
        checked_at = datetime.fromisoformat(meta.liveness_checked_at)
    except ValueError:
        return False
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - checked_at).total_seconds()
    return 0 <= age <= ttl_seconds


async def check_endpoint_liveness(
    sbom: "AiSbomDocument",
    client: "_InvokeClient",
    auth_headers: dict[str, str] | None = None,
    *,
    per_endpoint_timeout: float = 10.0,
    max_concurrent: int = 5,
    ttl_seconds: float | None = None,
    sbom_path: "Path | str | None" = None,
) -> LivenessReport:
    """Ping every attackable ``API_ENDPOINT`` node in *sbom* and record
    ``operational``/``liveness_checked_at``/``liveness_notes`` on each node's
    metadata in place.

    Nodes whose ``endpoint`` value fails :func:`_looks_like_rest_path` (bind
    address, MCP/SSE annotation, ...) are skipped entirely — never probed,
    left at ``operational=None`` — since there is no HTTP path to ping.
    Nodes flagged ``rate_limited`` are probed serially (never concurrently
    with each other) to avoid tripping the target's own rate limiter during
    what is meant to be a lightweight diagnostic pass.

    When *ttl_seconds* is given, a node whose cached
    ``operational``/``liveness_checked_at`` is still fresh (see
    :func:`_cached_liveness_is_fresh`) is not re-probed at all — this is what
    lets a redteam run after a behavior run (or vice versa) skip repeating the
    same live network round-trips, once both read/write the same enriched
    SBOM. When *sbom_path* is also given and at least one node's liveness was
    freshly probed (not purely served from cache), the updated *sbom* is
    persisted via :func:`~nuguard.common.auto_sbom_enricher.persist_liveness_sbom`
    so the next run benefits from this one's results. Passing neither
    parameter preserves the original always-probe, never-persist behavior.

    *auth_headers* is accepted for interface symmetry with other probe
    helpers in this package (e.g. :func:`~nuguard.common.endpoint_probe.probe_chat_endpoints`)
    but is not applied directly here — *client* is expected to already carry
    its configured auth headers (mirrors how :func:`~nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint`
    receives a pre-authenticated client).
    """
    from nuguard.sbom.types import ComponentType as _CT  # noqa: PLC0415

    report = LivenessReport()
    endpoint_nodes = [
        n for n in sbom.nodes if n.component_type == _CT.API_ENDPOINT and n.metadata
    ]

    concurrent_nodes = []
    serial_nodes = []
    any_freshly_probed = False
    for node in endpoint_nodes:
        meta = node.metadata
        if not _looks_like_rest_path(meta.endpoint):
            report.skipped += 1
            note = f"{node.name}: skipped (not an HTTP path — bind address / MCP-SSE annotation)."
            report.notes.append(note)
            continue
        if ttl_seconds is not None and _cached_liveness_is_fresh(meta, ttl_seconds):
            report.cached += 1
            note = (
                f"{node.name}: from enriched SBOM (checked {meta.liveness_checked_at}) "
                f"-> operational={meta.operational}"
            )
            report.notes.append(note)
            _log.info(
                "Endpoint liveness (from enriched SBOM, checked %s): %s -> %s",
                meta.liveness_checked_at,
                meta.endpoint,
                meta.operational,
            )
            continue
        if meta.rate_limited:
            serial_nodes.append(node)
        else:
            concurrent_nodes.append(node)

    semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def _run(node: object) -> None:
        nonlocal any_freshly_probed
        meta = node.metadata  # type: ignore[attr-defined]
        operational, note = await _ping_one(client, meta, per_endpoint_timeout=per_endpoint_timeout)
        meta.operational = operational
        meta.liveness_checked_at = _now_iso()
        meta.liveness_notes = [note]
        any_freshly_probed = True
        report.checked += 1
        if operational:
            report.operational += 1
        else:
            report.non_operational += 1
        report.notes.append(f"{node.name}: {note}")  # type: ignore[attr-defined]

    async def _run_bounded(node: object) -> None:
        async with semaphore:
            await _run(node)

    await asyncio.gather(*(_run_bounded(n) for n in concurrent_nodes))
    for node in serial_nodes:
        await _run(node)

    if sbom_path is not None and any_freshly_probed:
        from nuguard.common.auto_sbom_enricher import persist_liveness_sbom  # noqa: PLC0415

        try:
            persist_liveness_sbom(sbom, Path(sbom_path))
        except Exception as exc:  # noqa: BLE001
            _log.warning("Failed to persist endpoint liveness to enriched SBOM: %s", exc)

    return report


async def ensure_endpoint_liveness(
    sbom: "AiSbomDocument",
    client: "_InvokeClient",
    auth_headers: dict[str, str] | None = None,
    *,
    ttl_seconds: float = DEFAULT_LIVENESS_CACHE_TTL_SECONDS,
    per_endpoint_timeout: float = 10.0,
    max_concurrent: int = 5,
    sbom_path: "Path | str | None" = None,
) -> LivenessReport:
    """Cache-aware convenience wrapper around :func:`check_endpoint_liveness`.

    Prefer this over calling :func:`check_endpoint_liveness` directly from
    ``behavior``/``redteam`` bootstrap code — it always passes *ttl_seconds*
    and *sbom_path* through so a fresh cached result is honored and a newly
    probed one is persisted, which is what makes liveness checking a one-time
    cost across a behavior+redteam pair of runs against the same SBOM.
    """
    return await check_endpoint_liveness(
        sbom,
        client,
        auth_headers,
        per_endpoint_timeout=per_endpoint_timeout,
        max_concurrent=max_concurrent,
        ttl_seconds=ttl_seconds,
        sbom_path=sbom_path,
    )
