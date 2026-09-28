"""Live HTTP/WebSocket probing to discover which endpoint accepts chat requests.

This module owns the real implementation. When ``target_endpoint`` is not
explicitly configured, :func:`probe_chat_endpoints` cycles through candidate
paths, sends a lightweight test payload to each, and returns the first path
that yields a chat-like response.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from nuguard.common.endpoint_detection.constants import (
    CHAT_TEXT_SENTINEL,
    CONTENT_FIELD_NAMES,
    EXCLUDE_PATTERNS,
    HTTP_ENDPOINT_FALLBACK_PATHS,
    MESSAGE_HISTORY_KEYS,
    OPENAPI_CHAT_TOKENS,
    OPENAPI_PAYLOAD_KEYS,
    OPENAPI_SCHEMA_PATHS,
    PROBE_PAYLOADS,
    RUNTIME_NON_CHAT_KEYS,
    STREAMING_CONTENT_TYPES,
    TEST_MESSAGE,
    WEBSOCKET_ENDPOINT_FALLBACK_PATHS,
    normalize_payload_key,
)
from nuguard.common.endpoint_detection.sbom import _sbom_post_paths, _sbom_websocket_paths
from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.common.llm_client import LLMClient
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)


@dataclass
class ProbeResult:
    """Result of a successful chat endpoint probe.

    Supports 3-value tuple unpacking (``path, key, is_list = result``) so
    existing callers are unchanged. Access ``.value_template`` explicitly
    when you need the nested payload shape detected from OpenAPI schema.
    """

    path: str
    key: str
    is_list: bool
    # Payload value template built from OpenAPI schema when the chat key expects
    # a structured object (e.g. {"role": "user", "content": "..."}) rather than
    # a plain string. None means use a plain string (the default behaviour).
    value_template: "dict[str, object] | None" = field(default=None, compare=False)
    # False when the probe never saw a chat-like response and returned a
    # best-effort fallback (every shape 5xx'd or returned an error envelope).
    # Callers must not persist an unconfirmed result as if it were verified.
    confirmed: bool = field(default=True, compare=False)

    def __iter__(self):  # noqa: ANN204
        # Yield only the 3 positional fields so ``a, b, c = result`` still works.
        return iter((self.path, self.key, self.is_list))

    def __len__(self) -> int:
        return 3


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
    sensible defaults. The one field most likely to carry the chat message text
    is replaced with ``CHAT_TEXT_SENTINEL`` so the send path can substitute it
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
    for candidate in CONTENT_FIELD_NAMES:
        if candidate not in props:
            continue
        fs = props[candidate]
        if isinstance(fs, dict) and "$ref" in fs:
            fs = _resolve_openapi_ref(fs["$ref"], full_schema)
        ftype = fs.get("type") if isinstance(fs, dict) else None
        # Accept string or untyped fields as the text carrier.
        if ftype in ("string", None):
            template[candidate] = CHAT_TEXT_SENTINEL
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
    the request body schema for a known chat message field. ``value_template``
    is set when the chat key expects a structured message object (e.g. FastAPI
    ChatMessage with role+content fields) instead of a plain string. Returns
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
        if EXCLUDE_PATTERNS.search(path):
            continue

        score = sum(2 for tok in OPENAPI_CHAT_TOKENS if tok in path.lower())
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
        required_fields = set(body_schema.get("required") or [])
        for key in OPENAPI_PAYLOAD_KEYS:
            if key not in props:
                continue
            prop = props[key]
            if isinstance(prop, dict) and "$ref" in prop:
                prop = _resolve_openapi_ref(prop["$ref"], schema)
            is_list = isinstance(prop, dict) and prop.get("type") == "array"

            # Penalise a required field (other than the chat key) whose type
            # isn't a plain scalar — e.g. a required array of document/image
            # objects. Such a field can't be synthesised from plain probe
            # text, so the endpoint will reject a normal chat request even
            # though its path/key looks conversational (see the matching
            # penalty in discover_chat_candidates_from_sbom).
            path_score = score
            for other_field, other_schema in props.items():
                if other_field == key or other_field not in required_fields:
                    continue
                o = other_schema
                if isinstance(o, dict) and "$ref" in o:
                    o = _resolve_openapi_ref(o["$ref"], schema)
                o_type = o.get("type") if isinstance(o, dict) else None
                if o_type in ("string", "integer", "number", "boolean"):
                    continue
                path_score -= 6
                break

            if best is None or path_score > best[0]:
                best = (path_score, path, key, is_list)
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


def is_empty_session_response(response_text: str) -> bool:
    """Return True when *response_text* looks like an anonymous or empty-user session.

    These heuristics are intentionally broad — this is a warning signal for the
    caller to emit a config note, never a hard rejection. Covers common patterns
    across banking, healthcare, travel, and generic AI apps.
    """
    import re as _re

    if not response_text:
        return False
    text = response_text.lower()

    # Zero monetary balances (banking / fintech apps)
    if _re.search(r"\$0\.00", response_text) and "balance" in text:
        return True
    # KYC level 0 — unverified identity
    if _re.search(r"kyc.{0,20}level.{0,5}0", text) or _re.search(r"kyc.{0,5}:\s*0", text):
        return True
    # Account ID is "UNKNOWN" or "null"
    if _re.search(r"\bunknown\b", text) and any(
        w in text for w in ("account", "id", "user", "profile")
    ):
        return True
    # Explicit "no data" / "no account" / "no profile" phrases
    if _re.search(r"no\s+(data|records?|account|profile|transaction).{0,30}(file|found|available)", text):
        return True
    # Try JSON: if every numeric top-level value is 0 and string values are empty
    try:
        data = json.loads(response_text)
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
    llm: "LLMClient | None" = None,
) -> "ProbeResult | None":
    """Option 1: fetch OpenAPI/Swagger spec and verify the discovered endpoint.

    Uses per-request timeout of 5s so a missing spec never delays the pipeline.
    Returns a ProbeResult on success (including ``value_template`` when the
    schema reveals a nested message-object shape), ``None`` otherwise.
    """
    schema: dict | None = None
    for spec_path in OPENAPI_SCHEMA_PATHS:
        try:
            resp = await client.get(spec_path, timeout=min(timeout, 5.0))
            if resp.status_code != 200:
                continue
            candidate = resp.json()
            if isinstance(candidate, dict) and (
                "paths" in candidate or "openapi" in candidate or "swagger" in candidate
            ):
                _log.info("endpoint_detection: fetched OpenAPI schema from %s", spec_path)
                schema = candidate
                break
        except Exception:
            continue

    if schema is None:
        return None

    config = _chat_config_from_openapi(schema)
    if config is None:
        if llm is None:
            return None
        llm_res = await _llm_chat_key_from_openapi(schema, llm)
        if llm_res is None:
            return None
        oa_path, oa_key, oa_list = llm_res
        oa_template: "dict | None" = None
    else:
        oa_path, oa_key, oa_list, oa_template = config
    _log.info("endpoint_detection: OpenAPI config — path=%s key=%r list=%s template=%s",
              oa_path, oa_key, oa_list, bool(oa_template))

    # Use the detected template for the verification request when the schema
    # says the value must be a message object rather than a plain string.
    if oa_template is not None:
        # Replace the sentinel with the test message for the verification probe.
        val: object = {k: (TEST_MESSAGE if v == CHAT_TEXT_SENTINEL else v) for k, v in oa_template.items()}
    elif oa_list:
        val = [TEST_MESSAGE]
    else:
        val = TEST_MESSAGE
    body: dict[str, object] = {}
    if probe_payload_extras:
        body.update(probe_payload_extras)
    body[oa_key] = val
    try:
        resp = await client.post(oa_path, content=json.dumps(body))
    except Exception as exc:
        _log.debug("endpoint_detection: OpenAPI verify %s failed: %s", oa_path, exc)
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
            _log.info("endpoint_detection: OpenAPI selected %s (key=%r, status=%d)", oa_path, oa_key, status)
            return ProbeResult(oa_path, oa_key, oa_list, oa_template)
    elif status in (401, 403):
        # Auth-gated, not a rejected body — the endpoint exists and the
        # schema-specified key was structurally accepted, just needs
        # credentials. A plain 400/422 means the body itself was rejected
        # (e.g. a required field the probe couldn't fill), which is not a
        # confirmation and must fall through to let the blind probe /
        # browser-sniff fallback try other candidates instead.
        _log.info("endpoint_detection: OpenAPI selected %s (key=%r, status=%d)", oa_path, oa_key, status)
        return ProbeResult(oa_path, oa_key, oa_list, oa_template)
    # 400/404/405/422/5xx — don't block; let the blind probe try this path too
    return None


def _is_streaming_response(resp: "httpx.Response") -> bool:
    """Return True when the response Content-Type signals a streaming format."""
    ct = resp.headers.get("content-type", "").lower().split(";")[0].strip()
    return ct in STREAMING_CONTENT_TYPES


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


async def _llm_extract_error_field_names(body_text: str, llm: "LLMClient") -> list[str]:
    """Use LLM to extract a required field name from a non-standard 4xx error body."""
    try:
        prompt = (
            "An HTTP endpoint returned this error when sent a chat probe:\n"
            f"{body_text[:800]}\n\n"
            "What JSON field name does the request body need for the user message?\n"
            "Reply with ONLY the field name (e.g. 'query'). If unknown, reply: none"
        )
        text = (await llm.complete(prompt, label="probe-error-field")).strip().strip('"\' ').lower()
        if text and text != "none" and len(text) <= 64 and text.replace("_", "").replace("-", "").isalnum():
            return [text]
    except Exception:  # noqa: BLE001
        pass
    return []


async def _llm_chat_key_from_openapi(
    schema: dict, llm: "LLMClient"
) -> "tuple[str, str, bool] | None":
    """Use LLM to find a chat endpoint/key in an OpenAPI schema when structural parsing fails."""
    try:
        schema_text = json.dumps(schema, separators=(",", ":"))[:3000]
        prompt = (
            "Find the POST endpoint for chat/conversation in this OpenAPI schema:\n"
            f"{schema_text}\n\n"
            'Reply as JSON: {"path": "/endpoint", "key": "fieldName", "is_list": false}\n'
            "key = the request body field that holds the user message. Reply null if none."
        )
        text = (await llm.complete(prompt, label="probe-openapi-key")).strip()
        if text.lower() == "null":
            return None
        obj = json.loads(text)
        path = str(obj.get("path", ""))
        key = str(obj.get("key", ""))
        is_list = bool(obj.get("is_list", False))
        if path.startswith("/") and key:
            return path, key, is_list
    except Exception:  # noqa: BLE001
        pass
    return None


async def _llm_confirms_chat_response(data: dict, llm: "LLMClient") -> bool:
    """Ask LLM whether an ambiguous or non-standard response body is from a chat endpoint."""
    try:
        resp_text = json.dumps(data, separators=(",", ":"))[:600]
        prompt = (
            'A probe sent "hello" to an endpoint and got:\n'
            f"{resp_text}\n\n"
            "Is this from a chat/conversation AI endpoint? Reply: yes or no"
        )
        text = (await llm.complete(prompt, label="probe-chat-confirm")).strip().lower()
        return text.startswith("y")
    except Exception:  # noqa: BLE001
        return True  # safe default: don't block on LLM failure


def _has_known_chat_key(data: dict, response_key: str | None) -> bool:
    """True when *data* contains an explicit chat-response key (not just the ≥2-keys rule)."""
    if response_key and response_key in data:
        return True
    for key in (
        "response", "content", "prognosis", "text", "output",
        "answer", "result", "reply", "choices", "messages",
        "bot_response", "assistant_message", "assistant_reply",
        "generated_text", "completion", "delta", "llm_output", "llm_response", "data",
    ):
        if key in data:
            return True
    return False


def _extract_422_field_names(resp: "httpx.Response") -> list[str]:
    """Extract required body field names from a FastAPI/Pydantic 422 response.

    Parses ``{"detail": [{"loc": ["body", "field"], "msg": "..."}]}`` and returns
    the field names found under ``"body"`` in the loc array, filtered against the
    domain-key blocklist. Returns an empty list on any parse failure.
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
            field_name = str(loc[1])
            if field_name and field_name not in seen and normalize_payload_key(field_name) not in RUNTIME_NON_CHAT_KEYS:
                seen.add(field_name)
                names.append(field_name)
        return names
    except Exception:
        return []


# Error-envelope text that means the request *shape* was accepted and the
# app's own downstream dependency failed (LLM backend down, provider auth,
# timeouts). A shape that gets this far is a better fallback than one the
# app rejected outright.
_BACKEND_ERROR_RE = re.compile(
    r"econnrefused|econnreset|enotfound|cannot connect|connection (?:refused|reset|error)"
    r"|retryerror|failed after \d+ attempts|timed? ?out|timeout|rate.?limit|quota"
    r"|api[ _-]?key|unauthori[sz]ed|service unavailable|bad gateway|upstream|overloaded",
    re.IGNORECASE,
)
# Error-envelope text that means the app rejected the payload shape itself.
_SHAPE_REJECTED_RE = re.compile(
    r"must not be empty|cannot be empty|is required|required field|field required|missing"
    r"|invalid (?:prompt|input|request|body|payload)|must be (?:a|an) |expected (?:a|an) "
    r"|is not (?:a|an) |undefined|not iterable|cannot read propert",
    re.IGNORECASE,
)
# Field names an app names in its own validation error ("`messages` must not
# be empty", "missing field: query") — used to try that key next.
_ERROR_FIELD_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"[`'\"]?([A-Za-z_][A-Za-z0-9_]{1,40})[`'\"]?\s+(?:must not be empty|cannot be empty"
        r"|is required|is missing|must be provided|field required)",
        re.IGNORECASE,
    ),
    re.compile(
        r"missing (?:required )?(?:field|parameter|property|key)s?\s*[:=]?\s*[`'\"]?([A-Za-z_][A-Za-z0-9_]{1,40})",
        re.IGNORECASE,
    ),
)
_ERROR_FIELD_STOPWORDS = frozenset({"prompt", "input", "request", "body", "value", "field", "it", "this"})


def _error_envelope_text(data: dict) -> str:
    """Flatten an error envelope's values into one searchable string."""
    return " ".join(str(v) for v in data.values() if v is not None)


def _classify_error_envelope(data: dict) -> str:
    """Classify an error envelope as ``backend_error``, ``shape_rejected`` or ``unknown``.

    Backend failures are checked first: an app can only surface a downstream
    connection/provider error once it has accepted the request shape.
    """
    text = _error_envelope_text(data)
    if _BACKEND_ERROR_RE.search(text):
        return "backend_error"
    if _SHAPE_REJECTED_RE.search(text):
        return "shape_rejected"
    return "unknown"


# Higher is a better fallback candidate when no shape produced a chat response.
_ERROR_ENVELOPE_RANK = {"backend_error": 2, "unknown": 1, "shape_rejected": 0}


def _error_field_hints(data: dict) -> list[str]:
    """Return request field names an error envelope says are missing or empty."""
    text = _error_envelope_text(data)
    hints: list[str] = []
    for pattern in _ERROR_FIELD_RES:
        for match in pattern.finditer(text):
            name = match.group(1)
            if (
                name.lower() not in _ERROR_FIELD_STOPWORDS
                and normalize_payload_key(name) not in RUNTIME_NON_CHAT_KEYS
                and name not in hints
            ):
                hints.append(name)
    return hints


async def _blind_probe(
    client: "httpx.AsyncClient",
    paths: list[str],
    payload_shapes: "list[tuple[str, bool]]",
    known_response_key: str | None,
    probe_payload_extras: "dict[str, object] | None",
    *,
    known_payload_key: str | None = None,
    llm: "LLMClient | None" = None,
) -> "ProbeResult | None":
    """Fallback: try each path with each payload shape until one responds usefully."""
    server_error_fallback: ProbeResult | None = None
    streaming_error_fallback: ProbeResult | None = None
    streaming_error_rank = -1
    base = str(client.base_url).rstrip("/")

    for path in paths:
        _log.info("endpoint_detection: trying %s%s", base, path)
        tried_keys: set[str] = set()  # track all keys tried for this path
        # Mutable per-path queue: field names an error envelope names as
        # missing/empty are appended and tried after the configured shapes.
        shapes = list(payload_shapes)
        for pay_key, pay_list in shapes:
            tried_keys.add(pay_key)
            if pay_list and pay_key.strip().lower() in MESSAGE_HISTORY_KEYS:
                value: object = [{"role": "user", "content": TEST_MESSAGE}]
            elif pay_list:
                value = [TEST_MESSAGE]
            else:
                value = TEST_MESSAGE
            body: dict[str, object] = {}
            if probe_payload_extras:
                body.update(probe_payload_extras)
            body[pay_key] = value
            try:
                resp = await client.post(path, content=json.dumps(body))
            except Exception as exc:
                _log.debug("endpoint_detection: %s — request error: %s", path, exc)
                break  # network error; skip remaining shapes for this path

            status = resp.status_code
            if status in (404, 405):
                _log.debug("endpoint_detection: %s — %d (not found/method not allowed)", path, status)
                break  # try next path

            if status in (401, 403):
                # An auth rejection doesn't depend on the payload key — trying
                # the remaining shapes on this path can't fix it. Move to the
                # next candidate path instead of burning the rest of the sweep.
                _log.debug("endpoint_detection: %s — %d (auth rejected)", path, status)
                break

            if status == 429:
                # Target-wide condition, not a per-candidate one: getting
                # rate-limited on one path means the rest are likely to hit
                # the same quota. Stop probing entirely instead of continuing
                # to hammer the target (issue #532).
                from nuguard.common.errors import TargetRateLimitedError  # noqa: PLC0415

                retry_after_raw = resp.headers.get("Retry-After")
                retry_after: float | None = None
                if retry_after_raw is not None:
                    try:
                        retry_after = float(retry_after_raw)
                    except ValueError:
                        retry_after = None
                _log.warning("endpoint_detection: %s — 429 rate limited, aborting probe", path)
                raise TargetRateLimitedError(
                    f"Rate limited while probing {base}{path} (HTTP 429)",
                    url=f"{base}{path}",
                    retry_after=retry_after,
                )

            if status >= 500:
                _log.debug("endpoint_detection: %s — %d server error", path, status)
                if server_error_fallback is None:
                    server_error_fallback = ProbeResult(path, pay_key, pay_list, confirmed=False)
                continue  # try remaining shapes — correct key may still succeed

            if status < 300:
                try:
                    data = resp.json()
                except Exception:
                    data = _try_read_first_streaming_json(resp) or {}
                # A parsed body that is *only* an error envelope (e.g. a streaming
                # LLM backend's "messages must not be empty"/"invalid prompt" error
                # for the wrong payload shape) means the app logic rejected or
                # failed this request even though transport-level status and
                # content-type look fine. Never accept it as chat — not even via
                # the LLM confirm, which can mistake an "LLM error: ..." string
                # for a chat reply.
                is_error_envelope = (
                    isinstance(data, dict)
                    and bool(data)
                    and set(data.keys()) <= {"error", "detail", "message", "code", "status"}
                )
                chat_like = _looks_like_chat_response(data, known_response_key)
                if llm is not None and isinstance(data, dict) and data and not is_error_envelope:
                    # LLM re-checks ambiguous ≥2-key matches and catches non-standard response keys
                    if not chat_like or not _has_known_chat_key(data, known_response_key):
                        chat_like = await _llm_confirms_chat_response(data, llm)
                if chat_like:
                    _log.info("endpoint_detection: selected %s (key=%r, status=%d)", path, pay_key, status)
                    return ProbeResult(path, pay_key, pay_list)
                if is_error_envelope and not known_payload_key:
                    # The app may name the field it wanted ("`messages` must not
                    # be empty") — queue it so it's tried on this path next.
                    for hint_key in _error_field_hints(data):
                        if hint_key not in tried_keys and all(k != hint_key for k, _ in shapes):
                            shapes.append((hint_key, hint_key.lower() in MESSAGE_HISTORY_KEYS))
                if _is_streaming_response(resp):
                    if is_error_envelope:
                        # Keep trying other shapes, but remember the best one as
                        # a last-resort fallback in case every shape errors out:
                        # a shape the app accepted before a downstream failure
                        # (LLM backend unreachable) beats one it rejected.
                        kind = _classify_error_envelope(data)
                        _log.debug(
                            "endpoint_detection: %s key=%r → streaming error envelope (%s) %r, trying next shape",
                            path, pay_key, kind, data,
                        )
                        rank = _ERROR_ENVELOPE_RANK[kind]
                        if rank > streaming_error_rank:
                            streaming_error_rank = rank
                            streaming_error_fallback = ProbeResult(
                                path, pay_key, pay_list, confirmed=False
                            )
                        continue
                    # Streaming endpoint: accept even when we can't parse the body content
                    _log.info("endpoint_detection: selected %s (streaming, key=%r)", path, pay_key)
                    return ProbeResult(path, pay_key, pay_list)
                _log.debug("endpoint_detection: %s key=%r → %d but not chat-like", path, pay_key, status)
                continue

            # 4xx other than 404/405 — endpoint exists, payload shape may be wrong
            _log.debug("endpoint_detection: %s key=%r → %d (trying next shape)", path, pay_key, status)
            if known_payload_key:
                # Caller-specified key got 4xx — accept: endpoint is real, mismatch is config
                _log.info("endpoint_detection: selected %s (key=%r known, status=%d)", path, pay_key, status)
                return ProbeResult(path, pay_key, pay_list)

            # 422 — body tells us the correct field name; LLM handles non-FastAPI formats
            if status == 422:
                hint_keys = _extract_422_field_names(resp)
                if not hint_keys and llm is not None:
                    hint_keys = await _llm_extract_error_field_names(resp.text or "", llm)
                for hint_key in hint_keys:
                    if hint_key in tried_keys:
                        continue
                    tried_keys.add(hint_key)
                    hint_val: object = (
                        [{"role": "user", "content": TEST_MESSAGE}]
                        if hint_key.lower() in MESSAGE_HISTORY_KEYS
                        else TEST_MESSAGE
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
                                "endpoint_detection: 422-hint selected %s (key=%r, status=%d)",
                                path, hint_key, hint_status,
                            )
                            return ProbeResult(path, hint_key, hint_key.lower() in MESSAGE_HISTORY_KEYS)
                    elif hint_status not in (404, 405) and hint_status < 500:
                        _log.info(
                            "endpoint_detection: 422-hint selected %s (key=%r, status=%d)",
                            path, hint_key, hint_status,
                        )
                        return ProbeResult(path, hint_key, hint_key.lower() in MESSAGE_HISTORY_KEYS)

    _log.warning("endpoint_detection: no chat-capable endpoint found after probing %d paths", len(paths))
    if server_error_fallback:
        _log.info(
            "endpoint_detection: selected %s as fallback (5xx — payload_key=%r)",
            server_error_fallback.path, server_error_fallback.key,
        )
        return server_error_fallback
    if streaming_error_fallback:
        _log.info(
            "endpoint_detection: selected %s as unconfirmed fallback (streaming error envelope every shape — "
            "payload_key=%r list=%s)",
            streaming_error_fallback.path, streaming_error_fallback.key, streaming_error_fallback.is_list,
        )
        if streaming_error_rank == _ERROR_ENVELOPE_RANK["backend_error"]:
            _log.warning(
                "endpoint_detection: %s accepted payload_key=%r but the app reported a downstream "
                "failure (e.g. its LLM backend is unreachable) — the target cannot answer until "
                "that is fixed",
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
    llm: "LLMClient | None" = None,
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
            result = await _try_openapi_detection(client, timeout, known_response_key, probe_payload_extras, llm=llm)
            if result is not None:
                return result

            # Option 2: WebSocket upgrade probe — confirms WS-candidate paths via
            # a 101/426 response before falling back to the blind HTTP probe.
            for ws_path in ws_paths or ():
                if await _probe_websocket_upgrade(client, ws_path):
                    _log.info("endpoint_detection: detected WebSocket endpoint at %s", ws_path)
                    return ProbeResult(ws_path, "__websocket__", False)

        # Final: blind multi-shape probe (or single-shape when key is known)
        payload_shapes = (
            [(known_payload_key, known_payload_list)] if known_payload_key else PROBE_PAYLOADS
        )
        return await _blind_probe(
            client, paths, payload_shapes,
            known_response_key, probe_payload_extras,
            known_payload_key=known_payload_key,
            llm=llm,
        )


def _looks_like_chat_response(data: object, response_key: str | None = None) -> bool:
    """Return True if *data* looks like a processed response from a chat endpoint.

    For probe purposes the bar is intentionally low: any non-empty JSON object
    that is not a plain error envelope counts. We are discovering *which*
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


async def _verify_adk_list_apps(
    target_url: str,
    auth_headers: dict[str, str] | None,
    timeout: float,
) -> bool:
    """Positively verify *target_url* speaks ADK's ``/list-apps`` contract.

    Mirrors :meth:`~nuguard.redteam.target.framework_adapters.google_adk.
    GoogleADKAdapter._verify_contract` — a bare 200 response with a JSON list
    body (even empty) is the accepted signal; anything else (404, non-list
    body, connection failure) means "not ADK". Never raises.
    """
    from nuguard.redteam.target.framework_adapters.google_adk import _LIST_APPS_PATH

    base = target_url.rstrip("/")
    headers = dict(auth_headers or {})
    try:
        async with httpx.AsyncClient(base_url=base, headers=headers, timeout=timeout) as client:
            resp = await client.get(_LIST_APPS_PATH)
    except Exception as exc:
        _log.debug("endpoint_detection: ADK /list-apps verification request failed: %s", exc)
        return False
    if resp.status_code != 200:
        return False
    try:
        body = resp.json()
    except Exception:
        return False
    return isinstance(body, list)


async def probe_chat_endpoints(
    target_url: str,
    sbom: "AiSbomDocument | None",
    auth_headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    known_payload_key: str | None = None,
    known_payload_list: bool = False,
    known_response_key: str | None = None,
    probe_payload_extras: "dict[str, object] | None" = None,
    hint_path: str | None = None,
    llm: "LLMClient | None" = None,
) -> "ProbeResult | None":
    """Probe SBOM POST endpoints and return the first chat-capable one.

    Returns a :class:`ProbeResult` with ``(path, key, is_list)`` — supports
    tuple unpacking so existing callers are unchanged. Access
    ``result.value_template`` to get the nested payload shape when OpenAPI
    schema detection reveals the key expects a message object (e.g.
    ``{"role": "user", "content": "..."}`` instead of a plain string).

    When ``hint_path`` is provided (Option B), only that specific path is probed
    — detection discovers the payload key/list for a user-specified endpoint.
    When ``known_payload_key`` is supplied the detection pipeline is skipped and
    the probe verifies paths with that key only. ``sbom=None`` skips all
    SBOM-derived candidates and the ADK fast-path, probing only the generic
    ``HTTP_ENDPOINT_FALLBACK_PATHS``/``WEBSOCKET_ENDPOINT_FALLBACK_PATHS`` list
    (issue #532 — endpoint discovery must work without an SBOM).
    """
    paths = _sbom_post_paths(sbom)
    ws_paths = _sbom_websocket_paths(sbom)

    # ── ADK fast-path (framework shortcut — skip all detection) ──────────────
    # Google ADK uses a fixed RunAgentRequest protocol; the generic payload
    # shapes would always 422, so a plain probe loop can never confirm '/run'
    # on its own. Issue #552: SBOM framework evidence alone is not enough to
    # commit to this shortcut, though — a proxy app whose SBOM merely reports
    # ADK usage internally must not have its own traffic redirected to '/run'
    # with an unconsumed "__adk__" payload key (nothing downstream recognizes
    # that marker unless a real GoogleADKAdapter is also attached, which is
    # gated the same way in make_framework_adapter). Before taking the
    # shortcut, live-verify the target actually exposes an ADK-shaped
    # /list-apps response — the same positive-verification signal
    # GoogleADKAdapter itself requires. A failed/absent check falls through
    # to the normal generic detection loop below instead of guessing.
    from nuguard.redteam.target.framework_adapters.google_adk import (  # noqa: PLC0415
        _LIST_APPS_PATH,
        ADK_FRAMEWORK_NAMES,
    )
    summary = getattr(sbom, "summary", None)
    sbom_frameworks: list[str] = []
    if summary is not None:
        raw_frameworks = getattr(summary, "frameworks", None)
        if isinstance(raw_frameworks, (list, tuple)):
            sbom_frameworks = [str(f).lower() for f in raw_frameworks if f]
    if ADK_FRAMEWORK_NAMES & set(sbom_frameworks) and not hint_path:
        if await _verify_adk_list_apps(target_url, auth_headers, timeout):
            _log.info(
                "endpoint_detection: Google ADK detected in SBOM and live-verified "
                "via %s — skipping detection, using /run",
                _LIST_APPS_PATH,
            )
            return ProbeResult("/run", "__adk__", False)
        _log.info(
            "endpoint_detection: SBOM reports Google ADK, but %s did not "
            "confirm an ADK-shaped target — falling through to generic "
            "detection instead of assuming /run",
            _LIST_APPS_PATH,
        )

    # Always append common fallback paths so detection has candidates even when
    # the SBOM has no API_ENDPOINT nodes.
    for fallback in HTTP_ENDPOINT_FALLBACK_PATHS:
        if fallback not in paths:
            paths.append(fallback)
    for ws_fallback in WEBSOCKET_ENDPOINT_FALLBACK_PATHS:
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
        _log.debug("endpoint_detection: no probe-eligible paths")
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
        llm=llm,
    )


def normalize_probe_result(result: ProbeResult | None) -> ProbeResult | None:
    """Return a probe result unchanged while documenting the package boundary."""
    return result


async def probe_endpoint(
    target_url: str,
    sbom: Any,
    auth_headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    known_payload_key: str | None = None,
    known_payload_list: bool = False,
    known_response_key: str | None = None,
    probe_payload_extras: dict[str, object] | None = None,
    hint_path: str | None = None,
    llm: Any = None,
) -> ProbeResult | None:
    """Probe candidate routes and return endpoint plus payload metadata.

    This is the package entry point for full endpoint discovery and for targeted
    payload discovery when ``hint_path`` is supplied.
    """
    return await probe_chat_endpoints(
        target_url=target_url,
        sbom=sbom,
        auth_headers=auth_headers,
        timeout=timeout,
        known_payload_key=known_payload_key,
        known_payload_list=known_payload_list,
        known_response_key=known_response_key,
        probe_payload_extras=probe_payload_extras,
        hint_path=hint_path,
        llm=llm,
    )


async def probe_payload_shape(
    target_url: str,
    sbom: Any,
    endpoint: str,
    auth_headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    known_payload_list: bool = False,
    known_response_key: str | None = None,
    probe_payload_extras: dict[str, object] | None = None,
    llm: Any = None,
) -> ProbeResult | None:
    """Infer payload details for one known endpoint without changing its path."""
    return await probe_endpoint(
        target_url,
        sbom,
        auth_headers=auth_headers,
        timeout=timeout,
        known_payload_key=None,
        known_payload_list=known_payload_list,
        known_response_key=known_response_key,
        probe_payload_extras=probe_payload_extras,
        hint_path=endpoint,
        llm=llm,
    )

