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
import re
from typing import TYPE_CHECKING

import httpx

from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)

# Endpoint path fragments that are definitively NOT chat endpoints.
# Keep this list tight — false exclusions are worse than false inclusions
# because the probe will verify via HTTP anyway.
_EXCLUDE_PATTERNS = re.compile(
    r"/(health|ping|ready|live|metrics|auth|login|logout|register|signup|signin"
    r"|token|oauth|callback|webhook|static|assets|docs|openapi|swagger|favicon"
    r"|vite|upload|download)(/|$)",
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
    ("messages", True),
]

_TEST_MESSAGE = "Hello, this is a connectivity test."

# Well-known OpenAI/Anthropic/LangChain-style chat-history field names.
# When the probe tries one of these keys, the value must be a
# [{"role": "user", "content": ...}] message list, not a bare string/list.
_MESSAGE_HISTORY_KEYS: frozenset[str] = frozenset(
    {"messages", "history", "conversation", "chat_history"}
)

# OpenAPI/Swagger spec paths tried in priority order.
_OPENAPI_SCHEMA_PATHS: tuple[str, ...] = (
    "/openapi.json",
    "/v1/openapi.json",
    "/api/openapi.json",
    "/swagger.json",
    "/swagger/v1/swagger.json",
)

# Chat-signal tokens used to score OpenAPI endpoint paths.
_OPENAPI_CHAT_TOKENS: frozenset[str] = frozenset({
    "chat", "message", "messages", "completions", "complete", "generate",
    "infer", "query", "respond", "converse", "run", "agent", "llm", "ai",
})

# Priority order for chat message field names in OpenAPI request body schemas.
_OPENAPI_PAYLOAD_KEYS: tuple[str, ...] = (
    "messages", "message", "input", "query", "prompt", "text", "content", "msg",
)

# Payload keys that are definitively NOT conversational chat keys.
# These appear in domain-specific endpoints (banking transfers, healthcare orders, etc.)
# and must not be treated as the primary chat message field.
_RUNTIME_NON_CHAT_KEYS: frozenset[str] = frozenset({
    "from_account_id", "to_account_id", "amount", "card_id", "account_id",
    "patient_id", "patient_name", "order_id", "booking_reference", "flight_number",
    "transaction_id", "payment_id", "notification_id",
    "recipient_account", "source_account", "debit_account", "credit_account",
    "transfer_amount", "beneficiary_id", "invoice_id", "claim_id",
    "customer_name", "template_data",
})

_CAMEL_CASE_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _normalize_payload_key(key: str) -> str:
    """Normalize a payload key for comparison against _RUNTIME_NON_CHAT_KEYS.

    Converts camelCase (e.g. "patientName") to snake_case ("patient_name")
    and lowercases, so both spellings match a single blocklist entry.
    """
    return _CAMEL_CASE_RE.sub("_", key).lower()


def _resolve_openapi_ref(ref: str, schema: dict) -> dict:
    """Resolve a local $ref (e.g. '#/components/schemas/Foo') within *schema*."""
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return {}
    obj: object = schema
    for part in ref.lstrip("#/").split("/"):
        if not isinstance(obj, dict):
            return {}
        obj = obj.get(part, {})
    return obj if isinstance(obj, dict) else {}


def _chat_config_from_openapi(schema: dict) -> "tuple[str, str, bool] | None":
    """Extract ``(path, payload_key, payload_list)`` from an OpenAPI/Swagger schema.

    Scores every POST endpoint by chat-signal tokens in its path, then inspects
    the request body schema for a known chat message field.  Returns the
    highest-scoring match, or ``None`` when no chat-shaped POST endpoint is found.
    """
    paths_obj = schema.get("paths") or {}
    best: tuple[int, str, str, bool] | None = None  # (score, path, key, list)

    for path, methods in paths_obj.items():
        if not isinstance(methods, dict):
            continue
        post_op = methods.get("post")
        if not isinstance(post_op, dict):
            continue
        if _EXCLUDE_PATTERNS.search(path):
            continue

        score = sum(2 for tok in _OPENAPI_CHAT_TOKENS if tok in path.lower())
        if score == 0:
            continue

        # OpenAPI 3.x: requestBody.content["application/json"].schema
        body_schema: dict = {}
        req_body = post_op.get("requestBody") or {}
        json_content = (req_body.get("content") or {}).get("application/json") or {}
        body_schema = json_content.get("schema") or {}

        # Swagger 2.x: parameters[?in=body].schema
        if not body_schema:
            for param in post_op.get("parameters") or []:
                if isinstance(param, dict) and param.get("in") == "body":
                    body_schema = param.get("schema") or {}
                    break

        if "$ref" in body_schema:
            body_schema = _resolve_openapi_ref(body_schema["$ref"], schema)

        props = body_schema.get("properties") or {}
        for key in _OPENAPI_PAYLOAD_KEYS:
            if key not in props:
                continue
            prop = props[key]
            if isinstance(prop, dict) and "$ref" in prop:
                prop = _resolve_openapi_ref(prop["$ref"], schema)
            is_list = isinstance(prop, dict) and prop.get("type") == "array"
            if best is None or score > best[0]:
                best = (score, path, key, is_list)
            break

    if best is None:
        return None
    _, path, key, is_list = best
    return path, key, is_list


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
                if _EXCLUDE_PATTERNS.search(p):
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


def is_empty_session_response(response_text: str) -> bool:
    """Return True when *response_text* looks like an anonymous or empty-user session.

    These heuristics are intentionally broad — this is a warning signal for the
    caller to emit a config note, never a hard rejection.  Covers common patterns
    across banking, healthcare, travel, and generic AI apps.
    """
    if not response_text:
        return False
    text = response_text.lower()

    # Zero monetary balances (banking / fintech apps)
    if re.search(r"\$0\.00", response_text) and "balance" in text:
        return True
    # KYC level 0 — unverified identity
    if re.search(r"kyc.{0,20}level.{0,5}0", text) or re.search(r"kyc.{0,5}:\s*0", text):
        return True
    # Account ID is "UNKNOWN" or "null"
    if re.search(r"\bunknown\b", text) and any(
        w in text for w in ("account", "id", "user", "profile")
    ):
        return True
    # Explicit "no data" / "no account" / "no profile" phrases
    if re.search(r"no\s+(data|records?|account|profile|transaction).{0,30}(file|found|available)", text):
        return True
    # Try JSON: if every numeric top-level value is 0 and string values are empty
    try:
        import json as _json  # noqa: PLC0415
        data = _json.loads(response_text)
        if isinstance(data, dict) and data:
            nums = [v for v in data.values() if isinstance(v, (int, float))]
            strs = [v for v in data.values() if isinstance(v, str)]
            if nums and all(v == 0 for v in nums) and all(not v.strip() for v in strs if v):
                return True
    except Exception:
        pass

    return False


async def _try_openapi_detection(
    client: "httpx.AsyncClient",
    timeout: float,
    known_response_key: str | None,
    probe_payload_extras: "dict[str, object] | None",
) -> "tuple[str, str, bool] | None":
    """Option 1: fetch OpenAPI/Swagger spec and verify the discovered endpoint.

    Uses per-request timeout of 5s so a missing spec never delays the pipeline.
    Returns ``(path, payload_key, payload_list)`` on success, ``None`` otherwise.
    """
    schema: dict | None = None
    for spec_path in _OPENAPI_SCHEMA_PATHS:
        try:
            resp = await client.get(spec_path, timeout=min(timeout, 5.0))
            if resp.status_code != 200:
                continue
            candidate = resp.json()
            if isinstance(candidate, dict) and (
                "paths" in candidate or "openapi" in candidate or "swagger" in candidate
            ):
                _log.info("endpoint_probe: fetched OpenAPI schema from %s", spec_path)
                schema = candidate
                break
        except Exception:
            continue

    if schema is None:
        return None

    config = _chat_config_from_openapi(schema)
    if config is None:
        return None

    oa_path, oa_key, oa_list = config
    _log.info("endpoint_probe: OpenAPI config — path=%s key=%r list=%s", oa_path, oa_key, oa_list)

    val: object = [_TEST_MESSAGE] if oa_list else _TEST_MESSAGE
    body: dict[str, object] = {}
    if probe_payload_extras:
        body.update(probe_payload_extras)
    body[oa_key] = val
    try:
        resp = await client.post(oa_path, content=json.dumps(body))
    except Exception as exc:
        _log.debug("endpoint_probe: OpenAPI verify %s failed: %s", oa_path, exc)
        return None

    status = resp.status_code
    if status in (404, 405):
        return None
    if status < 300:
        try:
            data = resp.json()
        except Exception:
            data = {}
        if _looks_like_chat_response(data, known_response_key):
            _log.info("endpoint_probe: OpenAPI selected %s (key=%r, status=%d)", oa_path, oa_key, status)
            return oa_path, oa_key, oa_list
    elif 400 <= status < 500:
        # 4xx (not 404/405) — endpoint exists, schema-specified key accepted
        _log.info("endpoint_probe: OpenAPI selected %s (key=%r, status=%d)", oa_path, oa_key, status)
        return oa_path, oa_key, oa_list
    # 5xx — don't block; let the blind probe try this path too
    return None


async def _blind_probe(
    client: "httpx.AsyncClient",
    paths: list[str],
    payload_shapes: "list[tuple[str, bool]]",
    known_response_key: str | None,
    probe_payload_extras: "dict[str, object] | None",
    *,
    known_payload_key: str | None = None,
) -> "tuple[str, str, bool] | None":
    """Fallback: try each path with each payload shape until one responds usefully."""
    server_error_fallback: tuple[str, str, bool] | None = None
    base = str(client.base_url).rstrip("/")

    for path in paths:
        _log.info("endpoint_probe: trying %s%s", base, path)
        for pay_key, pay_list in payload_shapes:
            if pay_list and pay_key.strip().lower() in _MESSAGE_HISTORY_KEYS:
                value: object = [{"role": "user", "content": _TEST_MESSAGE}]
            elif pay_list:
                value = [_TEST_MESSAGE]
            else:
                value = _TEST_MESSAGE
            body: dict[str, object] = {}
            if probe_payload_extras:
                body.update(probe_payload_extras)
            body[pay_key] = value
            try:
                resp = await client.post(path, content=json.dumps(body))
            except Exception as exc:
                _log.debug("endpoint_probe: %s — request error: %s", path, exc)
                break  # network error; skip remaining shapes for this path

            status = resp.status_code
            if status in (404, 405):
                _log.debug("endpoint_probe: %s — %d (not found/method not allowed)", path, status)
                break  # try next path

            if status >= 500:
                _log.debug("endpoint_probe: %s — %d server error", path, status)
                if server_error_fallback is None:
                    server_error_fallback = (path, pay_key, pay_list)
                break  # try next path

            if status < 300:
                try:
                    data = resp.json()
                except Exception:
                    data = {}
                if _looks_like_chat_response(data, known_response_key):
                    _log.info("endpoint_probe: selected %s (key=%r, status=%d)", path, pay_key, status)
                    return path, pay_key, pay_list
                _log.debug("endpoint_probe: %s key=%r → %d but not chat-like", path, pay_key, status)
                continue

            # 4xx other than 404/405 — endpoint exists, payload shape may be wrong
            _log.debug("endpoint_probe: %s key=%r → %d (trying next shape)", path, pay_key, status)
            if known_payload_key:
                # Caller-specified key got 4xx — accept: endpoint is real, mismatch is config
                _log.info("endpoint_probe: selected %s (key=%r known, status=%d)", path, pay_key, status)
                return path, pay_key, pay_list

    _log.warning("endpoint_probe: no chat-capable endpoint found after probing %d paths", len(paths))
    if server_error_fallback:
        p_path, p_key, p_list = server_error_fallback
        _log.info(
            "endpoint_probe: selected %s as fallback (5xx — payload_key=%r)", p_path, p_key,
        )
        return server_error_fallback
    return None


async def _detect_chat_endpoint(
    base: str,
    paths: list[str],
    headers: "dict[str, str]",
    timeout: float,
    known_response_key: str | None,
    probe_payload_extras: "dict[str, object] | None",
    known_payload_key: str | None = None,
    known_payload_list: bool = False,
) -> "tuple[str, str, bool] | None":
    """Ordered detection pipeline — smarter options first, blind probe as final fallback.

    When ``known_payload_key`` is set the detection options are skipped and the
    blind probe runs immediately with that single shape (the caller already knows
    the key; we just need to confirm which path accepts it).
    """
    async with httpx.AsyncClient(
        base_url=base,
        timeout=httpx.Timeout(timeout),
        headers=headers,
        follow_redirects=True,
    ) as client:
        if not known_payload_key:
            # Option 1: OpenAPI/Swagger schema
            result = await _try_openapi_detection(client, timeout, known_response_key, probe_payload_extras)
            if result is not None:
                return result

            # Option 2+: future detection options go here

        # Final: blind multi-shape probe (or single-shape when key is known)
        payload_shapes = (
            [(known_payload_key, known_payload_list)] if known_payload_key else _PROBE_PAYLOADS
        )
        return await _blind_probe(
            client, paths, payload_shapes,
            known_response_key, probe_payload_extras,
            known_payload_key=known_payload_key,
        )


async def probe_chat_endpoints(
    target_url: str,
    sbom: "AiSbomDocument",
    auth_headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    known_payload_key: str | None = None,
    known_payload_list: bool = False,
    known_response_key: str | None = None,
    probe_payload_extras: "dict[str, object] | None" = None,
) -> "tuple[str, str, bool] | None":
    """Probe SBOM POST endpoints and return the first chat-capable one.

    Returns ``(path, payload_key, payload_list)`` for the winning endpoint, or
    ``None`` if no endpoint responded usefully.

    When ``known_payload_key`` is supplied the detection pipeline is skipped and
    the probe verifies paths with that key only (the caller already knows the
    shape; this just confirms which path accepts it).
    """
    paths = _sbom_post_paths(sbom)

    # ── ADK fast-path (framework shortcut — skip all detection) ──────────────
    # Google ADK uses a fixed RunAgentRequest protocol; the generic payload
    # shapes would always 422. Return the well-known '/run' path immediately.
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
        _log.info("endpoint_probe: Google ADK detected in SBOM — skipping detection, using /run")
        return "/run", "__adk__", False

    # Always append common fallback paths so detection has candidates even when
    # the SBOM has no API_ENDPOINT nodes.
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

    return await _detect_chat_endpoint(
        base, paths, headers, timeout,
        known_response_key, probe_payload_extras,
        known_payload_key=known_payload_key,
        known_payload_list=known_payload_list,
    )


def discover_chat_candidates_from_sbom(
    sbom: "AiSbomDocument",
    chat_path: str = "",
    chat_payload_key: str = "message",
    chat_payload_list: bool = False,
) -> list[tuple[str, str, bool, str | None]]:
    """Return all chat-capable SBOM endpoints sorted by score descending.

    Each element is ``(path, payload_key, payload_list, response_key)``.
    Returns an empty list when no candidates are found — callers should then
    fall back to :func:`probe_chat_endpoints` for live HTTP discovery.

    This function is zero-I/O.  See :func:`discover_chat_config_from_sbom` for
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
        if meta.method and meta.method.upper() != "POST":
            continue

        discovered_path = meta.endpoint or chat_path
        endpoint_l = discovered_path.lower()
        source = (meta.extras or {}).get("source")

        # ── Resolve payload key ────────────────────────────────────────────
        inferred_response_key: str | None = meta.response_text_key or None
        if meta.chat_payload_key:
            if _normalize_payload_key(meta.chat_payload_key) in _RUNTIME_NON_CHAT_KEYS:
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
        score = 0
        if source == "auto_enrichment":
            score -= 2
        elif source == "runtime_probe":
            score -= 1
        else:
            score += 3

        if node.confidence >= 0.9:
            score += 2
        elif node.confidence >= 0.75:
            score += 1

        if "/chat/message" in endpoint_l:
            score += 2
        elif any(token in endpoint_l for token in ("/chat/queue", "/messages", "/message", "/generate", "/completions", "/respond", "/query")):
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

        # Penalise nodes confirmed dead by the live probe — GET 404 means the
        # route doesn't exist at all on the deployed target; POST 405 strongly
        # suggests the path is handled by a different mechanism (e.g. static file
        # serving on Azure SWA, not the API backend).
        extras = meta.extras or {}
        if extras.get("probe_get_404"):
            score -= 8
        if extras.get("probe_post_405"):
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


def discover_chat_config_from_sbom(
    sbom: "AiSbomDocument",
    chat_path: str = "",
    chat_payload_key: str = "message",
    chat_payload_list: bool = False,
) -> tuple[str, str, bool, str | None]:
    """Auto-discover the best chat endpoint config from SBOM API_ENDPOINT node metadata.

    Returns ``(chat_path, chat_payload_key, chat_payload_list, response_text_key)``.
    ``response_text_key`` is ``None`` when not determinable from the SBOM.

    This function is zero-I/O (no network calls).  Use :func:`probe_chat_endpoints`
    for live HTTP probing when the SBOM lacks sufficient metadata.  Use
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
