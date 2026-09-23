"""SBOM-only (zero-I/O) endpoint and payload discovery.

This module owns the real implementation of SBOM-based chat-candidate
scoring. Legacy names (``discover_chat_candidates_from_sbom``,
``discover_chat_config_from_sbom``, ``sbom_indicates_websocket``,
``_sbom_post_paths``) are kept for backward compatibility with existing
callers/tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

from nuguard.common.endpoint_detection.constants import (
    EXCLUDE_PATTERNS,
    HAS_PATH_PARAM_RE,
    PROBE_SOURCE_AUTO_ENRICHMENT,
    PROBE_SOURCE_RUNTIME_PROBE,
    RUNTIME_NON_CHAT_KEYS,
    ProbeExtras,
    has_required_structured_field,
    normalize_payload_key,
)
from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)


def _confirmation_is_fresh(confirmed_at: str | None, ttl_seconds: float) -> bool:
    """True when *confirmed_at* (ISO-8601) is within *ttl_seconds* of now.

    Mirrors ``nuguard.common.endpoint_liveness._cached_liveness_is_fresh``.
    """
    if not confirmed_at:
        return False
    try:
        checked_at = datetime.fromisoformat(confirmed_at)
    except ValueError:
        return False
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - checked_at).total_seconds()
    return 0 <= age <= ttl_seconds


def find_confirmed_chat_endpoint(
    sbom: "AiSbomDocument",
    *,
    expected_path: str | None = None,
    ttl_seconds: float | None = None,
) -> tuple[str, str, bool, str | None] | None:
    """Return the endpoint a prior behavior/redteam run confirmed live and
    persisted into the enriched SBOM, or ``None`` if there is no such
    confirmation (or it has expired — see *ttl_seconds*).

    Unlike SBOM keyword ranking (:func:`_sbom_post_paths`), this scans every
    ``API_ENDPOINT`` node for the ``runtime_probe`` marker directly — the
    confirmed endpoint's path never has to *look* chat-like (e.g. ``/extract``
    is found here even though it scores zero in keyword ranking).

    Args:
        sbom: The (ideally enriched) SBOM to scan.
        expected_path: When given, only a confirmation for this exact path is
            returned — used when the caller already has an explicit endpoint
            configured and only wants to know if *that* endpoint is confirmed
            (skipping a redundant live probe), never to discover a different one.
        ttl_seconds: When given, a confirmation older than this is treated as
            expired (returns ``None``) so a stale/moved endpoint eventually
            gets re-verified instead of being trusted forever.

    Returns:
        ``(path, payload_key, payload_list, response_key)`` or ``None``.
    """
    from nuguard.sbom.models import NodeType  # noqa: PLC0415

    for node in sbom.nodes:
        meta = node.metadata
        if (
            node.component_type != NodeType.API_ENDPOINT
            or meta is None
            or meta.chat_payload_key is None
        ):
            continue
        if expected_path is not None and meta.endpoint != expected_path:
            continue
        extras = meta.extras or {}
        if extras.get("source") != PROBE_SOURCE_RUNTIME_PROBE:
            continue
        if ttl_seconds is not None and not _confirmation_is_fresh(
            extras.get("confirmed_at"), ttl_seconds
        ):
            continue
        endpoint = meta.endpoint or expected_path or ""
        return endpoint, meta.chat_payload_key, bool(meta.chat_payload_list), meta.response_text_key
    return None


def _sbom_websocket_paths(sbom: "AiSbomDocument | None") -> list[str]:
    """Return WebSocket endpoint paths declared in the SBOM.

    These come from API_ENDPOINT nodes with ``metadata.method == "WEBSOCKET"``
    (e.g. FastAPI ``@app.websocket(...)`` routes detected by the SBOM adapter).
    ``None`` (no SBOM available) returns an empty list — callers fall back to
    the generic path list.
    """
    if sbom is None:
        return []

    from nuguard.sbom.models import NodeType  # noqa: PLC0415

    paths: list[str] = []
    for node in sbom.nodes:
        if node.component_type != NodeType.API_ENDPOINT:
            continue
        meta = node.metadata
        if not meta or not meta.method or meta.method.upper() != "WEBSOCKET":
            continue
        path: str = (meta.endpoint or "").strip()
        if not path or not path.startswith("/"):
            continue
        if HAS_PATH_PARAM_RE.search(path):
            continue
        if path not in paths:
            paths.append(path)
    return paths


def _sbom_post_paths(sbom: "AiSbomDocument | None") -> list[str]:
    """Return POST endpoint paths from the SBOM, scored by chat-likelihood.

    ``None`` (no SBOM available) returns an empty list — callers fall back to
    the generic path list.
    """
    if sbom is None:
        return []

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
        # Skip parameterised paths like /user/{id}, /chat/:id, /items/<pk>
        if HAS_PATH_PARAM_RE.search(path):
            continue
        # Skip non-POST
        if meta.method and meta.method.upper() not in ("POST", ""):
            continue

        if EXCLUDE_PATTERNS.search(path):
            continue

        path_l = path.lower()
        score = 0
        for token in ("langgraph", "run", "chat", "message", "query", "complete",
                       "infer", "generate", "respond", "agent", "converse", "talk",
                       "assistant", "llm", "ai"):
            if token in path_l:
                score += 2
                break

        # Path token match is required — key presence alone never qualifies an endpoint.
        if score == 0:
            continue

        # Key as a small tie-breaker only when it is a plausible chat key.
        if meta.chat_payload_key:
            if normalize_payload_key(meta.chat_payload_key) not in RUNTIME_NON_CHAT_KEYS:
                score += 1
        if node.confidence >= 0.9:
            score += 1

        scored.append((score, path))

    # Supplement with paths from summary.api_endpoints when no API_ENDPOINT nodes
    # are present (e.g. SBOM was generated without full static analysis).
    if not scored:
        summary = getattr(sbom, "summary", None)
        raw_api_eps = getattr(summary, "api_endpoints", None) if summary is not None else None
        if isinstance(raw_api_eps, (list, tuple)):
            seen: set[str] = set()
            for p in raw_api_eps:
                p = str(p).strip()
                # Skip wildcards and non-path entries
                if not p or not p.startswith("/") or "{" in p or p == "/*":
                    continue
                if EXCLUDE_PATTERNS.search(p):
                    continue
                if p not in seen:
                    seen.add(p)
                    path_l = p.lower()
                    score = 0
                    for token in ("chat", "agent", "run", "message", "query",
                                   "complete", "infer", "generate", "respond",
                                   "converse", "talk", "assistant", "llm", "ai"):
                        if token in path_l:
                            score += 2
                            break
                    scored.append((score, p))

    # Sort descending by score, then alphabetically for determinism
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [p for _, p in scored]


def discover_chat_candidates_from_sbom(
    sbom: "AiSbomDocument",
    chat_path: str = "",
    chat_payload_key: str = "message",
    chat_payload_list: bool = False,
) -> list[tuple[str, str, bool, str | None]]:
    """Return all chat-capable SBOM endpoints sorted by score descending.

    Each element is ``(path, payload_key, payload_list, response_key)``.
    Returns an empty list when no candidates are found — callers should then
    fall back to live probing.

    This function is zero-I/O. See :func:`discover_chat_config_from_sbom` for
    the single-winner convenience wrapper.
    """
    from nuguard.sbom.models import NodeType  # noqa: PLC0415

    # Detect which AI frameworks are present in the SBOM.
    summary = getattr(sbom, "summary", None)
    sbom_frameworks: set[str] = set()
    if summary is not None:
        raw = getattr(summary, "frameworks", None)
        if isinstance(raw, (list, tuple)):
            sbom_frameworks = {str(f).lower() for f in raw if f}
    has_langgraph = bool(sbom_frameworks & {"langgraph"})

    candidates: list[tuple[int, str, str, bool, str, str | None]] = []
    for node in sbom.nodes:
        if node.component_type != NodeType.API_ENDPOINT:
            continue
        meta = node.metadata
        if not meta:
            continue
        method_u = (meta.method or "").upper()
        if method_u == "WEBSOCKET":
            ws_path = (meta.endpoint or "").strip()
            if not ws_path or not ws_path.startswith("/"):
                continue
            ws_score = 3 if node.confidence >= 0.9 else 1
            # Penalise (don't drop) path-param WS routes — same treatment as
            # the HTTP branch below: a thread/session-scoped stream route
            # (e.g. /api/threads/{thread_id}/browser/stream) is still a valid
            # fallback candidate when nothing parameter-free is available;
            # path_param_sources (when declared) lets the bootstrap resolve it.
            if HAS_PATH_PARAM_RE.search(ws_path):
                ws_score -= 5
            candidates.append((ws_score, ws_path, "__websocket__", False, node.name, None))
            continue
        # "ANY" means the static adapter couldn't resolve the HTTP verb (Go
        # net/http and gorilla/mux dispatch on r.Method at runtime; Java
        # servlet mappings are similar) — not "confirmed not POST" the way a
        # real GET/PUT/DELETE is. Treat it the same as blank/unset, not the
        # same as a definitively wrong method.
        if method_u and method_u not in ("POST", "ANY"):
            continue

        discovered_path = meta.endpoint or chat_path
        endpoint_l = discovered_path.lower()
        node_extras: ProbeExtras = cast(ProbeExtras, meta.extras or {})
        source = node_extras.get("source")

        # ── Resolve payload key ────────────────────────────────────────────
        inferred_response_key: str | None = meta.response_text_key or None
        if meta.chat_payload_key:
            if normalize_payload_key(meta.chat_payload_key) in RUNTIME_NON_CHAT_KEYS:
                # Domain-specific key (financial, medical, etc.) — not a chat endpoint.
                # Skip so that summary.api_endpoints fallback can find the real one.
                continue
            payload_key = meta.chat_payload_key
            payload_list = bool(meta.chat_payload_list)
        elif has_langgraph and any(
            tok in endpoint_l
            for tok in ("/run_langgraph", "/run_graph", "/langgraph/run")
        ):
            # LangGraph convention: POST {"phrases": ["..."]} → response["prognosis"]
            payload_key = "phrases"
            payload_list = True
            if inferred_response_key is None:
                inferred_response_key = "prognosis"
        else:
            # No payload info and no matching framework convention — skip this node.
            continue

        # ── Scoring ────────────────────────────────────────────────────────
        # Path must contain a clearly conversational signal — broad tokens like
        # "ai", "run", "agent", "query" are excluded because they match too many
        # non-chat paths (e.g. "aibom", "run-redteam", "agents/list").
        _CHAT_PATH_TOKENS = (
            "chat", "message", "completions", "converse", "respond",
            "infer", "generate", "llm", "assistant", "langgraph",
        )
        if not any(tok in endpoint_l for tok in _CHAT_PATH_TOKENS):
            continue

        score = 0
        if source == PROBE_SOURCE_AUTO_ENRICHMENT:
            score -= 2
        elif source == PROBE_SOURCE_RUNTIME_PROBE:
            score -= 1
        else:
            score += 3

        if node.confidence >= 0.9:
            score += 2
        elif node.confidence >= 0.75:
            score += 1

        # Path tokens must match whole segments — a substring check would let
        # e.g. "/respond-visual" falsely match the "/respond" token and
        # outscore the real "/chat" endpoint.
        endpoint_segments = [s for s in endpoint_l.strip("/").split("/") if s]

        def _segment_match(token: str) -> bool:
            tok_segments = [s for s in token.strip("/").split("/") if s]
            n = len(tok_segments)
            return any(
                endpoint_segments[i : i + n] == tok_segments
                for i in range(len(endpoint_segments) - n + 1)
            )

        if _segment_match("/chat/message"):
            score += 2
        elif any(
            _segment_match(token)
            for token in ("/chat/queue", "/messages", "/message", "/generate", "/completions", "/respond", "/query")
        ):
            score += 3
        elif endpoint_l.endswith("/chat"):
            score += 1

        # LangGraph run endpoint is always the primary agent interface.
        if "run_langgraph" in endpoint_l or "run_graph" in endpoint_l:
            score += 3

        if endpoint_l.startswith("/api/"):
            score += 1
        if inferred_response_key:
            score += 1

        # Penalise nodes that had no explicit payload key (inferred).
        if not meta.chat_payload_key:
            score -= 1

        # Penalise endpoints that require another structured field (e.g. a list
        # of document/image pages) beyond the chat payload key — a plain-text
        # probe request can't populate it, so the endpoint will reject every
        # attack turn with a validation error (e.g. HTTP 400/422) regardless of
        # how conversational its path or payload key name looks.
        if has_required_structured_field(meta.request_body_schema, payload_key):
            score -= 6

        # Penalise path-param routes — they require a real resource ID and will
        # 404 with an unresolved placeholder. Still returned so callers can fall
        # back to them when no parameter-free option exists. The penalty is
        # much smaller when every param has a known bootstrap source (see
        # nuguard/common/endpoint_preflight.py's _bootstrap_path_params) —
        # those routes actually get resolved before use, so they shouldn't be
        # scored as if they were permanently broken.
        if HAS_PATH_PARAM_RE.search(discovered_path):
            params = meta.path_params or []
            sources = meta.path_param_sources or {}
            if params and all(p in sources for p in params):
                score -= 1
            else:
                score -= 5

        # Penalise nodes confirmed dead by the live probe — GET 404 means the
        # route doesn't exist at all on the deployed target; POST 405 strongly
        # suggests the path is handled by a different mechanism (e.g. static file
        # serving on Azure SWA, not the API backend).
        if node_extras.get("probe_get_404"):
            score -= 8
        if node_extras.get("probe_post_405"):
            score -= 6

        candidates.append(
            (score, discovered_path, payload_key, payload_list, node.name, inferred_response_key)
        )

    if not candidates:
        return []

    # Sort descending by score, then alphabetically for determinism
    candidates.sort(key=lambda item: (-item[0], item[1]))
    _log.info(
        "SBOM chat candidates (%d): %s",
        len(candidates),
        [(c[1], c[2]) for c in candidates[:5]],
    )
    return [(c[1], c[2], c[3], c[5]) for c in candidates]


def sbom_indicates_websocket(
    sbom: "AiSbomDocument | None",
    chat_path: str = "",
    chat_payload_key: str = "message",
) -> bool:
    """Return True if *chat_path* (or the top SBOM candidate, when unset) is a WebSocket route.

    Zero-I/O — used to decide, *before* any bootstrap/live-probe network call,
    whether to open a WS handshake or send an HTTP POST. Errors are swallowed
    (treated as "not WebSocket") since this is a best-effort pre-check; the
    fuller live-probe-based discovery elsewhere still applies afterwards.
    """
    if chat_payload_key == "__websocket__":
        return True
    if sbom is None:
        return False
    try:
        candidates = discover_chat_candidates_from_sbom(sbom, chat_path=chat_path)
    except Exception:
        return False
    if chat_path:
        return any(path == chat_path and key == "__websocket__" for path, key, _l, _r in candidates)
    return bool(candidates) and candidates[0][1] == "__websocket__"


def discover_chat_config_from_sbom(
    sbom: "AiSbomDocument",
    chat_path: str = "",
    chat_payload_key: str = "message",
    chat_payload_list: bool = False,
) -> tuple[str, str, bool, str | None]:
    """Auto-discover the best chat endpoint config from SBOM API_ENDPOINT node metadata.

    Returns ``(chat_path, chat_payload_key, chat_payload_list, response_text_key)``.
    ``response_text_key`` is ``None`` when not determinable from the SBOM.

    This function is zero-I/O (no network calls). Use live probing for cases
    where the SBOM lacks sufficient metadata. Use
    :func:`discover_chat_candidates_from_sbom` when you need the full ranked list
    for endpoint rotation.
    """
    # Explicit endpoint is authoritative and must never be overridden by SBOM.
    if chat_path:
        return chat_path, chat_payload_key, chat_payload_list, None

    candidates = discover_chat_candidates_from_sbom(sbom, chat_path, chat_payload_key, chat_payload_list)
    if candidates:
        best = candidates[0]
        _log.info(
            "SBOM auto-discovered chat config: path=%s key=%s list=%s response_key=%s",
            best[0], best[1], best[2], best[3],
        )
        return best

    # No API_ENDPOINT nodes — fall back to summary.api_endpoints when available.
    summary = getattr(sbom, "summary", None)
    raw_api_eps = getattr(summary, "api_endpoints", None) if summary is not None else None
    if isinstance(raw_api_eps, (list, tuple)):
        # "chat"-family tokens are a near-definitive signal of the conversational
        # endpoint; "agent"-family tokens are common on unrelated listing/management
        # routes (e.g. "/api/agents" that just enumerates configured agents) and
        # must not outweigh a real chat path just because it happens to sort earlier
        # in the SBOM's endpoint list.
        strong_tokens = ("chat", "message", "converse", "respond", "complete", "generate")
        weak_tokens = ("agent", "run", "query", "infer", "talk", "assistant", "llm", "ai")
        best_path: str | None = None
        best_score = -1
        for p in raw_api_eps:
            p = str(p).strip()
            if not p or not p.startswith("/") or "{" in p or p == "/*":
                continue
            p_l = p.lower()
            last_segment = p_l.rstrip("/").rsplit("/", 1)[-1]
            score = sum(3 for tok in strong_tokens if tok in p_l)
            score += sum(1 for tok in weak_tokens if tok in p_l)
            if last_segment in strong_tokens:
                score += 3
            if p_l.startswith("/api/"):
                score += 1
            if score > best_score:
                best_score = score
                best_path = p
        if best_path and best_score > 0:
            _log.info(
                "SBOM summary.api_endpoints fallback — using chat path %s", best_path,
            )
            return best_path, chat_payload_key, chat_payload_list, None

    return chat_path, chat_payload_key, chat_payload_list, None


# ---------------------------------------------------------------------------
# Package-facing adapters (existing public names used by resolver.py etc.)
# ---------------------------------------------------------------------------

def discover_chat_candidates(sbom: Any, **kwargs: Any) -> list[Any]:
    """Return SBOM candidates ranked by the chat-likelihood scorer above."""
    return list(discover_chat_candidates_from_sbom(sbom, **kwargs))


def discover_chat_config(
    sbom: Any,
    chat_path: str | None = None,
    chat_payload_key: str = "message",
    chat_payload_list: bool = False,
) -> tuple[str | None, str, bool, str | None]:
    """Return the SBOM-selected path and payload metadata."""
    return discover_chat_config_from_sbom(
        sbom,
        chat_path=chat_path or "",
        chat_payload_key=chat_payload_key,
        chat_payload_list=chat_payload_list,
    )


def indicates_websocket(
    sbom: Any,
    chat_path: str | None = None,
    chat_payload_key: str = "message",
) -> bool:
    """Return whether SBOM metadata identifies the selected route as WebSocket."""
    return bool(
        sbom_indicates_websocket(
            sbom,
            chat_path=chat_path or "",
            chat_payload_key=chat_payload_key,
        )
    )
