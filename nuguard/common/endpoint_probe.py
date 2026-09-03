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

import base64
import json
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)


@dataclass
class ProbeResult:
    """Result of a successful chat endpoint probe.

    Supports 3-value tuple unpacking (``path, key, is_list = result``) so
    existing callers are unchanged.  Access ``.value_template`` explicitly
    when you need the nested payload shape detected from OpenAPI schema.
    """

    path: str
    key: str
    is_list: bool
    # Payload value template built from OpenAPI schema when the chat key expects
    # a structured object (e.g. {"role": "user", "content": "..."}) rather than
    # a plain string.  None means use a plain string (the default behaviour).
    value_template: "dict[str, object] | None" = field(default=None, compare=False)

    def __iter__(self):  # noqa: ANN204
        # Yield only the 3 positional fields so ``a, b, c = result`` still works.
        return iter((self.path, self.key, self.is_list))

    def __len__(self) -> int:
        return 3

# Detects path-parameter placeholders — FastAPI/Express {id}, NestJS :id, or <id>.
_HAS_PATH_PARAM_RE = re.compile(r"(:\w+|\{\w+\}|<\w+>)")

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

# Fallback WebSocket paths tried when the SBOM has no declared WS endpoints.
_WS_FALLBACK_PATHS: tuple[str, ...] = ("/ws", "/ws/chat", "/socket", "/realtime")

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
    # Tool/action dispatch keys — these are agentic action endpoints, not chat endpoints.
    "tool_name", "tool_call", "tool_id", "action", "action_name", "action_type",
})

_CAMEL_CASE_RE = re.compile(r"(?<!^)(?=[A-Z])")

# Sentinel placed in the value_template dict for the field that carries the
# actual chat text.  Replaced at send-time regardless of the field name.
_CHAT_TEXT_SENTINEL = "__nuguard_chat_text__"

# String field names that most likely carry the chat message text, in priority
# order.  The first match wins the sentinel in _build_object_template.
_CONTENT_FIELD_NAMES: tuple[str, ...] = (
    "content", "text", "message", "body", "input", "prompt", "query", "msg",
)


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


def _build_object_template(
    prop_schema: dict,
    full_schema: dict,
    depth: int = 0,
) -> "dict[str, object] | None":
    """Recursively build a template dict from an OpenAPI object property schema.

    Returns ``None`` when the schema does not describe an object (i.e. it is a
    plain string, integer, etc.) so callers can fall back to a bare string.

    Required fields and known-content-name string fields are included with
    sensible defaults.  The one field most likely to carry the chat message text
    is replaced with ``_CHAT_TEXT_SENTINEL`` so the send path can substitute it
    without knowing the field name in advance.
    """
    if depth > 2:  # guard against pathologically deep schemas
        return None
    resolved = prop_schema
    if isinstance(resolved, dict) and "$ref" in resolved:
        resolved = _resolve_openapi_ref(resolved["$ref"], full_schema)
    if not isinstance(resolved, dict):
        return None
    # Only proceed for object schemas (explicit type or a properties block).
    if resolved.get("type") not in ("object", None):
        return None
    props = resolved.get("properties") or {}
    if not props:
        return None

    required: set[str] = set(resolved.get("required") or [])
    template: dict[str, object] = {}
    sentinel_placed = False

    # First pass: place the sentinel on the best content-carrying field.
    for candidate in _CONTENT_FIELD_NAMES:
        if candidate not in props:
            continue
        fs = props[candidate]
        if isinstance(fs, dict) and "$ref" in fs:
            fs = _resolve_openapi_ref(fs["$ref"], full_schema)
        ftype = fs.get("type") if isinstance(fs, dict) else None
        # Accept string or untyped fields as the text carrier.
        if ftype in ("string", None):
            template[candidate] = _CHAT_TEXT_SENTINEL
            sentinel_placed = True
            break

    if not sentinel_placed:
        # No recognised content field — this schema probably isn't a message object.
        return None

    # Second pass: fill remaining required fields with type-appropriate defaults.
    for field_name, field_schema in props.items():
        if field_name in template:
            continue  # sentinel field already placed
        if field_name not in required:
            continue
        fs = field_schema
        if isinstance(fs, dict) and "$ref" in fs:
            fs = _resolve_openapi_ref(fs["$ref"], full_schema)
        ftype = fs.get("type") if isinstance(fs, dict) else None
        fdefault = fs.get("default") if isinstance(fs, dict) else None
        fenums = fs.get("enum") if isinstance(fs, dict) else None
        if fdefault is not None:
            template[field_name] = fdefault
        elif fenums and isinstance(fenums, list) and fenums:
            template[field_name] = fenums[0]  # use first enum value as default
        elif ftype == "string":
            template[field_name] = ""
        elif ftype in ("integer", "number"):
            template[field_name] = 0
        elif ftype == "boolean":
            template[field_name] = False
        elif ftype == "array":
            template[field_name] = []
        elif ftype == "object" or (isinstance(fs, dict) and "properties" in fs):
            nested = _build_object_template(fs, full_schema, depth + 1)
            if nested is not None:
                template[field_name] = nested
        # Unknown/null types are skipped

    return template


def _chat_config_from_openapi(schema: dict) -> "tuple[str, str, bool, dict | None] | None":
    """Extract ``(path, key, is_list, value_template)`` from an OpenAPI/Swagger schema.

    Scores every POST endpoint by chat-signal tokens in its path, then inspects
    the request body schema for a known chat message field.  ``value_template``
    is set when the chat key expects a structured message object (e.g. FastAPI
    ChatMessage with role+content fields) instead of a plain string.  Returns
    ``None`` when no chat-shaped POST endpoint is found.
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

    # When the chat key resolves to an object schema, build a generic template
    # so the probe and redteam client send the correct nested structure.
    value_template: dict[str, object] | None = None
    raw_prop = (body_schema.get("properties") or {}).get(key) or {}
    if not is_list:
        value_template = _build_object_template(raw_prop, schema)

    return path, key, is_list, value_template


def _sbom_websocket_paths(sbom: "AiSbomDocument") -> list[str]:
    """Return WebSocket endpoint paths declared in the SBOM.

    These come from API_ENDPOINT nodes with ``metadata.method == "WEBSOCKET"``
    (e.g. FastAPI ``@app.websocket(...)`` routes detected by the SBOM adapter).
    """
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
        if _HAS_PATH_PARAM_RE.search(path):
            continue
        if path not in paths:
            paths.append(path)
    return paths


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
        # Skip parameterised paths like /user/{id}, /chat/:id, /items/<pk>
        if _HAS_PATH_PARAM_RE.search(path):
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

        # Path token match is required — key presence alone never qualifies an endpoint.
        if score == 0:
            continue

        # Key as a small tie-breaker only when it is a plausible chat key.
        if meta.chat_payload_key:
            if _normalize_payload_key(meta.chat_payload_key) not in _RUNTIME_NON_CHAT_KEYS:
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
) -> "ProbeResult | None":
    """Option 1: fetch OpenAPI/Swagger spec and verify the discovered endpoint.

    Uses per-request timeout of 5s so a missing spec never delays the pipeline.
    Returns a ProbeResult on success (including ``value_template`` when the
    schema reveals a nested message-object shape), ``None`` otherwise.
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

    oa_path, oa_key, oa_list, oa_template = config
    _log.info("endpoint_probe: OpenAPI config — path=%s key=%r list=%s template=%s",
              oa_path, oa_key, oa_list, bool(oa_template))

    # Use the detected template for the verification request when the schema
    # says the value must be a message object rather than a plain string.
    if oa_template is not None:
        # Replace the sentinel with the test message for the verification probe.
        val: object = {k: (_TEST_MESSAGE if v == _CHAT_TEXT_SENTINEL else v) for k, v in oa_template.items()}
    elif oa_list:
        val = [_TEST_MESSAGE]
    else:
        val = _TEST_MESSAGE
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
            data = _try_read_first_streaming_json(resp) or {}
        if _looks_like_chat_response(data, known_response_key) or _is_streaming_response(resp):
            _log.info("endpoint_probe: OpenAPI selected %s (key=%r, status=%d)", oa_path, oa_key, status)
            return ProbeResult(oa_path, oa_key, oa_list, oa_template)
    elif 400 <= status < 500:
        # 4xx (not 404/405) — endpoint exists, schema-specified key accepted
        _log.info("endpoint_probe: OpenAPI selected %s (key=%r, status=%d)", oa_path, oa_key, status)
        return ProbeResult(oa_path, oa_key, oa_list, oa_template)
    # 5xx — don't block; let the blind probe try this path too
    return None


_STREAMING_CONTENT_TYPES: frozenset[str] = frozenset({
    "text/event-stream",
    "application/x-ndjson",
    "application/ndjson",
    "application/jsonl",
    "application/x-jsonlines",
})


def _is_streaming_response(resp: "httpx.Response") -> bool:
    """Return True when the response Content-Type signals a streaming format."""
    ct = resp.headers.get("content-type", "").lower().split(";")[0].strip()
    return ct in _STREAMING_CONTENT_TYPES


def _try_read_first_streaming_json(resp: "httpx.Response") -> "dict | None":
    """Extract the first JSON object from an SSE or NDJSON response body.

    Handles SSE (``data: {...}`` lines) and NDJSON (first non-empty line).
    Returns ``None`` when the response is not a streaming type or unparseable.
    """
    if not _is_streaming_response(resp):
        return None
    try:
        for line in resp.text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("data:"):  # SSE prefix
                line = line[5:].strip()
                if line == "[DONE]":
                    continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue
    except Exception:
        pass
    return None


def _extract_422_field_names(resp: "httpx.Response") -> list[str]:
    """Extract required body field names from a FastAPI/Pydantic 422 response.

    Parses ``{"detail": [{"loc": ["body", "field"], "msg": "..."}]}`` and returns
    the field names found under ``"body"`` in the loc array, filtered against the
    domain-key blocklist.  Returns an empty list on any parse failure.
    """
    try:
        detail = resp.json().get("detail")
        if not isinstance(detail, list):
            return []
        names: list[str] = []
        seen: set[str] = set()
        for item in detail:
            if not isinstance(item, dict):
                continue
            loc = item.get("loc")
            if not isinstance(loc, (list, tuple)) or len(loc) < 2 or str(loc[0]) != "body":
                continue
            field = str(loc[1])
            if field and field not in seen and _normalize_payload_key(field) not in _RUNTIME_NON_CHAT_KEYS:
                seen.add(field)
                names.append(field)
        return names
    except Exception:
        return []


async def _blind_probe(
    client: "httpx.AsyncClient",
    paths: list[str],
    payload_shapes: "list[tuple[str, bool]]",
    known_response_key: str | None,
    probe_payload_extras: "dict[str, object] | None",
    *,
    known_payload_key: str | None = None,
) -> "ProbeResult | None":
    """Fallback: try each path with each payload shape until one responds usefully."""
    server_error_fallback: ProbeResult | None = None
    streaming_error_fallback: ProbeResult | None = None
    base = str(client.base_url).rstrip("/")

    for path in paths:
        _log.info("endpoint_probe: trying %s%s", base, path)
        tried_keys: set[str] = set()  # track all keys tried for this path
        for pay_key, pay_list in payload_shapes:
            tried_keys.add(pay_key)
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
                    server_error_fallback = ProbeResult(path, pay_key, pay_list)
                continue  # try remaining shapes — correct key may still succeed

            if status < 300:
                try:
                    data = resp.json()
                except Exception:
                    data = _try_read_first_streaming_json(resp) or {}
                if _looks_like_chat_response(data, known_response_key):
                    _log.info("endpoint_probe: selected %s (key=%r, status=%d)", path, pay_key, status)
                    return ProbeResult(path, pay_key, pay_list)
                # A parsed body that is *only* an error envelope (e.g. a streaming
                # LLM backend's "messages must not be empty"/"invalid prompt" error
                # for the wrong payload shape) means this shape was rejected by the
                # app logic even though transport-level status/content-type look
                # fine. Don't accept it — keep trying other shapes, but remember it
                # as a last-resort fallback in case every shape errors out.
                is_error_envelope = (
                    isinstance(data, dict)
                    and bool(data)
                    and set(data.keys()) <= {"error", "detail", "message", "code", "status"}
                )
                if _is_streaming_response(resp):
                    if is_error_envelope:
                        _log.debug(
                            "endpoint_probe: %s key=%r → streaming but error envelope %r, trying next shape",
                            path, pay_key, data,
                        )
                        if streaming_error_fallback is None:
                            streaming_error_fallback = ProbeResult(path, pay_key, pay_list)
                        continue
                    # Streaming endpoint: accept even when we can't parse the body content
                    _log.info("endpoint_probe: selected %s (streaming, key=%r)", path, pay_key)
                    return ProbeResult(path, pay_key, pay_list)
                _log.debug("endpoint_probe: %s key=%r → %d but not chat-like", path, pay_key, status)
                continue

            # 4xx other than 404/405 — endpoint exists, payload shape may be wrong
            _log.debug("endpoint_probe: %s key=%r → %d (trying next shape)", path, pay_key, status)
            if known_payload_key:
                # Caller-specified key got 4xx — accept: endpoint is real, mismatch is config
                _log.info("endpoint_probe: selected %s (key=%r known, status=%d)", path, pay_key, status)
                return ProbeResult(path, pay_key, pay_list)

            # 422 — FastAPI/Pydantic validation error: body tells us the correct field name
            if status == 422:
                for hint_key in _extract_422_field_names(resp):
                    if hint_key in tried_keys:
                        continue
                    tried_keys.add(hint_key)
                    hint_val: object = (
                        [{"role": "user", "content": _TEST_MESSAGE}]
                        if hint_key.lower() in _MESSAGE_HISTORY_KEYS
                        else _TEST_MESSAGE
                    )
                    hint_body: dict[str, object] = {}
                    if probe_payload_extras:
                        hint_body.update(probe_payload_extras)
                    hint_body[hint_key] = hint_val
                    try:
                        hint_resp = await client.post(path, content=json.dumps(hint_body))
                    except Exception:
                        continue
                    hint_status = hint_resp.status_code
                    if hint_status < 300:
                        try:
                            hint_data = hint_resp.json()
                        except Exception:
                            hint_data = {}
                        if _looks_like_chat_response(hint_data, known_response_key):
                            _log.info(
                                "endpoint_probe: 422-hint selected %s (key=%r, status=%d)",
                                path, hint_key, hint_status,
                            )
                            return ProbeResult(path, hint_key, False)
                    elif hint_status not in (404, 405) and hint_status < 500:
                        _log.info(
                            "endpoint_probe: 422-hint selected %s (key=%r, status=%d)",
                            path, hint_key, hint_status,
                        )
                        return ProbeResult(path, hint_key, False)

    _log.warning("endpoint_probe: no chat-capable endpoint found after probing %d paths", len(paths))
    if server_error_fallback:
        _log.info(
            "endpoint_probe: selected %s as fallback (5xx — payload_key=%r)",
            server_error_fallback.path, server_error_fallback.key,
        )
        return server_error_fallback
    if streaming_error_fallback:
        _log.info(
            "endpoint_probe: selected %s as fallback (streaming error envelope every shape — payload_key=%r)",
            streaming_error_fallback.path, streaming_error_fallback.key,
        )
        return streaming_error_fallback
    return None


def compute_websocket_accept(key: str) -> str:
    """Compute the RFC 6455 §4.2.2 ``Sec-WebSocket-Accept`` value for *key*.

    ``base64(sha1(key + "258EAFA455E4B4CE-C5B58384CD9835B4"))`` — the value a
    genuine WebSocket server must echo back to prove it actually validated the
    handshake, rather than just answering 101/426 unconditionally (used both
    by the live upgrade probe and by tests that need to mock a real response).
    """
    import hashlib  # noqa: PLC0415

    digest = hashlib.sha1((key + "258EAFA455E4B4CE-C5B58384CD9835B4").encode()).digest()
    return base64.b64encode(digest).decode()


async def _probe_websocket_upgrade(client: httpx.AsyncClient, path: str) -> bool:
    """Return True if *path* answers an HTTP Upgrade request as a WebSocket endpoint.

    httpx cannot complete a real WS handshake, but the response can still be
    verified per RFC 6455 §4.2.2: a genuine WS server responds 101 with a
    ``Sec-WebSocket-Accept`` header equal to
    ``compute_websocket_accept(key)``. A bare 101 (or 426) with no matching
    Accept header is NOT trusted — some infrastructure (e.g. Cloud Run's
    front-end proxy) performs a generic HTTP/1.1 Upgrade handshake for *any*
    path regardless of whether the application actually implements WebSocket
    there, which produced a false positive against a real deployed app that
    has no WebSocket route at all. A 426 (Upgrade Required) is accepted only
    when its ``Upgrade`` header explicitly names ``websocket`` (RFC 9110
    §15.5.22) — still not a full guarantee, but the best signal available for
    a rejected-but-WS-aware server.
    """
    ws_key = base64.b64encode(os.urandom(16)).decode()
    expected_accept = compute_websocket_accept(ws_key)
    headers = {
        "Connection": "Upgrade",
        "Upgrade": "websocket",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": ws_key,
    }
    try:
        resp = await client.get(path, headers=headers)
    except Exception:  # noqa: BLE001 — any transport failure means "not WS here"
        return False
    if resp.status_code == 101:
        return resp.headers.get("sec-websocket-accept", "") == expected_accept
    if resp.status_code == 426:
        return "websocket" in resp.headers.get("upgrade", "").lower()
    return False


async def _detect_chat_endpoint(
    base: str,
    paths: list[str],
    headers: "dict[str, str]",
    timeout: float,
    known_response_key: str | None,
    probe_payload_extras: "dict[str, object] | None",
    known_payload_key: str | None = None,
    known_payload_list: bool = False,
    ws_paths: list[str] | None = None,
) -> "ProbeResult | None":
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

            # Option 2: WebSocket upgrade probe — confirms WS-candidate paths via
            # a 101/426 response before falling back to the blind HTTP probe.
            for ws_path in ws_paths or ():
                if await _probe_websocket_upgrade(client, ws_path):
                    _log.info("endpoint_probe: detected WebSocket endpoint at %s", ws_path)
                    return ProbeResult(ws_path, "__websocket__", False)

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
    hint_path: str | None = None,
) -> "ProbeResult | None":
    """Probe SBOM POST endpoints and return the first chat-capable one.

    Returns a :class:`ProbeResult` with ``(path, key, is_list)`` — supports
    tuple unpacking so existing callers are unchanged.  Access
    ``result.value_template`` to get the nested payload shape when OpenAPI
    schema detection reveals the key expects a message object (e.g.
    ``{"role": "user", "content": "..."}`` instead of a plain string).

    When ``hint_path`` is provided (Option B), only that specific path is probed
    — detection discovers the payload key/list for a user-specified endpoint.
    When ``known_payload_key`` is supplied the detection pipeline is skipped and
    the probe verifies paths with that key only.
    """
    paths = _sbom_post_paths(sbom)
    ws_paths = _sbom_websocket_paths(sbom)

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
    if ADK_FRAMEWORK_NAMES & set(sbom_frameworks) and not hint_path:
        _log.info("endpoint_probe: Google ADK detected in SBOM — skipping detection, using /run")
        return ProbeResult("/run", "__adk__", False)

    # Always append common fallback paths so detection has candidates even when
    # the SBOM has no API_ENDPOINT nodes.
    for fallback in ("/chat", "/run", "/api/chat", "/v1/chat", "/query", "/agent"):
        if fallback not in paths:
            paths.append(fallback)
    for ws_fallback in _WS_FALLBACK_PATHS:
        if ws_fallback not in ws_paths:
            ws_paths.append(ws_fallback)

    # Option B: caller provided a specific path — probe only that path to detect
    # the payload key, ignoring SBOM paths and fallbacks entirely. Still check
    # it as a WS candidate (a user-configured target_endpoint may itself be a
    # WebSocket route) instead of dropping WS detection altogether.
    if hint_path:
        paths = [hint_path]
        ws_paths = [hint_path]
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
        ws_paths=ws_paths,
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
            if _HAS_PATH_PARAM_RE.search(ws_path):
                ws_score -= 5
            candidates.append((ws_score, ws_path, "__websocket__", False, node.name, None))
            continue
        if method_u and method_u != "POST":
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

        # Penalise path-param routes — they require a real resource ID and will
        # 404 with an unresolved placeholder. Still returned so callers can fall
        # back to them when no parameter-free option exists.
        if _HAS_PATH_PARAM_RE.search(discovered_path):
            score -= 5

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
    for key in (
        "response", "content", "prognosis", "text", "output",
        "answer", "result", "reply", "choices", "messages",
        # common custom agent / HuggingFace / LangChain response keys
        "bot_response", "assistant_message", "assistant_reply",
        "generated_text", "completion", "delta",
        "llm_output", "llm_response", "data",
    ):
        if key in data:
            return True
    return False


# ---------------------------------------------------------------------------
# Frontend-bundle API-origin discovery
# ---------------------------------------------------------------------------

# Detects a served page that's a bundled SPA (Vite/CRA/webpack), not a JSON API.
_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src=["\']([^"\']+\.js[^"\']*)["\']', re.IGNORECASE)

# Matches the common "baseURL: '<absolute-url>'" shape client bundles bake in.
_API_BASE_URL_RE = re.compile(
    r'(?:baseURL|baseUrl|apiBaseUrl|apiBase|apiUrl|API_URL|API_BASE_URL)\s*[:=]\s*'
    r'[`"\'](https?://[^`"\'\s,)]+)',
)

_MAX_SCRIPTS_TO_SCAN = 6
_SCRIPT_FETCH_TIMEOUT = 15.0

_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


async def discover_api_origin_from_frontend_bundle(
    target_url: str,
) -> "tuple[str | None, list[str]]":
    """Best-effort: find a separate-origin API base URL baked into a served SPA bundle.

    Returns ``(origin, notes)``. *origin* is ``scheme://host[:port]`` when a different
    origin than *target_url* was found baked into a script; otherwise ``None``.
    Never raises — network/parse failures return ``(None, [])``.
    """
    from urllib.parse import urljoin, urlparse  # noqa: PLC0415

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as http_client:
            resp = await http_client.get(target_url)
    except Exception as exc:
        _log.debug("discover_api_origin_from_frontend_bundle: GET %s failed: %s", target_url, exc)
        return None, []

    html = resp.text or ""
    script_srcs = _SCRIPT_SRC_RE.findall(html)[:_MAX_SCRIPTS_TO_SCAN]
    if not script_srcs:
        return None, []

    target_parsed = urlparse(target_url)
    target_host_port = (target_parsed.hostname, target_parsed.port)

    for src in script_srcs:
        script_url = urljoin(target_url.rstrip("/") + "/", src)
        try:
            async with httpx.AsyncClient(
                timeout=_SCRIPT_FETCH_TIMEOUT, follow_redirects=True
            ) as http_client:
                script_resp = await http_client.get(script_url)
        except Exception as exc:
            _log.debug("discover_api_origin_from_frontend_bundle: GET script %s failed: %s", script_url, exc)
            continue

        match = _API_BASE_URL_RE.search(script_resp.text or "")
        if not match:
            continue

        parsed = urlparse(match.group(1))
        if (parsed.hostname, parsed.port) == target_host_port:
            continue

        if (parsed.hostname or "").lower() in _LOOPBACK_HOSTNAMES:
            target_hostname = target_parsed.hostname
            if not target_hostname or parsed.port is None or parsed.port == target_host_port[1]:
                _log.warning(
                    "discover_api_origin_from_frontend_bundle: ignoring loopback origin "
                    "%r baked into %r — not reachable from this process",
                    match.group(1), script_url,
                )
                continue
            scheme = target_parsed.scheme or parsed.scheme
            origin = f"{scheme}://{target_hostname}:{parsed.port}"
            note = (
                f"Target URL {target_url!r} serves a frontend bundle whose baked-in API "
                f"origin {match.group(1)!r} uses a loopback host — reusing target hostname "
                f"with discovered port {parsed.port} instead ({origin!r})."
            )
            _log.warning("discover_api_origin_from_frontend_bundle: %s", note)
            return origin, [note]

        origin = f"{parsed.scheme}://{parsed.netloc}"
        note = (
            f"Target URL {target_url!r} serves a frontend bundle with no proxied API "
            f"surface; discovered API origin {origin!r} baked into {script_url!r}. "
            f"Set target.url in nuguard.yaml to this origin directly to skip this scan."
        )
        _log.warning("discover_api_origin_from_frontend_bundle: %s", note)
        return origin, [note]

    return None, []
