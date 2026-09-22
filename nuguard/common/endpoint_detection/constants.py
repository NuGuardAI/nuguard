"""Constants shared by endpoint and payload detection strategies."""

from __future__ import annotations

import re
from typing import Any, TypedDict


class _UnsetType:
    """Sentinel type used when a configuration field was not supplied."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _UnsetType()

DEFAULT_PAYLOAD_KEY = "message"
DEFAULT_PAYLOAD_LIST = False

HTTP_ENDPOINT_FALLBACK_PATHS = (
    "/chat",
    "/run",
    "/api/chat",
    "/v1/chat",
    "/query",
    "/agent",
)

WEBSOCKET_ENDPOINT_FALLBACK_PATHS = (
    "/ws",
    "/ws/chat",
    "/socket",
    "/realtime",
)

# These statuses indicate that a candidate path did not accept the attempted request.
ROTATION_STATUS_CODES = frozenset({400, 404, 405, 422})

# These statuses mean the target responded and therefore is not dead for liveness purposes.
REACHABLE_AUTH_STATUS_CODES = frozenset({401, 403})

DEFAULT_PROBE_TIMEOUT_SECONDS = 15.0

# Message sent to the target during discovery/verification probes.
TEST_MESSAGE = "Hello, this is a connectivity test."

# Detects path-parameter placeholders — FastAPI/Express {id}, NestJS :id, or <id>.
HAS_PATH_PARAM_RE = re.compile(r"(:\w+|\{\w+\}|<\w+>)")

# Endpoint path fragments that are definitively NOT chat endpoints.
# Keep this list tight — false exclusions are worse than false inclusions
# because the probe will verify via HTTP anyway.
EXCLUDE_PATTERNS = re.compile(
    r"/(health|ping|ready|live|metrics|auth|login|logout|register|signup|signin"
    r"|token|oauth|callback|webhook|static|assets|docs|openapi|swagger|favicon"
    r"|vite|upload|download)(/|$)",
    re.IGNORECASE,
)

# Payload shapes to attempt when we don't know the key. Tried in order.
PROBE_PAYLOADS: list[tuple[str, bool]] = [
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

# Well-known OpenAI/Anthropic/LangChain-style chat-history field names.
# When the probe tries one of these keys, the value must be a
# [{"role": "user", "content": ...}] message list, not a bare string/list.
MESSAGE_HISTORY_KEYS: frozenset[str] = frozenset(
    {"messages", "history", "conversation", "chat_history"}
)

# OpenAPI/Swagger spec paths tried in priority order.
OPENAPI_SCHEMA_PATHS: tuple[str, ...] = (
    "/openapi.json",
    "/v1/openapi.json",
    "/api/openapi.json",
    "/swagger.json",
    "/swagger/v1/swagger.json",
)

# Chat-signal tokens used to score OpenAPI endpoint paths.
OPENAPI_CHAT_TOKENS: frozenset[str] = frozenset({
    "chat", "message", "messages", "completions", "complete", "generate",
    "infer", "query", "respond", "converse", "run", "agent", "llm", "ai",
})

# Priority order for chat message field names in OpenAPI request body schemas.
OPENAPI_PAYLOAD_KEYS: tuple[str, ...] = (
    "messages", "message", "input", "query", "prompt", "text", "content", "msg",
)

# Payload keys that are definitively NOT conversational chat keys.
# These appear in domain-specific endpoints (banking transfers, healthcare orders, etc.)
# and must not be treated as the primary chat message field.
RUNTIME_NON_CHAT_KEYS: frozenset[str] = frozenset({
    "from_account_id", "to_account_id", "amount", "card_id", "account_id",
    "patient_id", "patient_name", "order_id", "booking_reference", "flight_number",
    "transaction_id", "payment_id", "notification_id",
    "recipient_account", "source_account", "debit_account", "credit_account",
    "transfer_amount", "beneficiary_id", "invoice_id", "claim_id",
    "customer_name", "template_data",
    # Tool/action dispatch keys — these are agentic action endpoints, not chat endpoints.
    "tool_name", "tool_call", "tool_id", "action", "action_name", "action_type",
})

CAMEL_CASE_RE = re.compile(r"(?<!^)(?=[A-Z])")

# Sentinel placed in the value_template dict for the field that carries the
# actual chat text. Replaced at send-time regardless of the field name.
CHAT_TEXT_SENTINEL = "__nuguard_chat_text__"

# String field names that most likely carry the chat message text, in priority
# order. The first match wins the sentinel in the OpenAPI object-template builder.
CONTENT_FIELD_NAMES: tuple[str, ...] = (
    "content", "text", "message", "body", "input", "prompt", "query", "msg",
)

SIMPLE_SCALAR_TYPES: frozenset[str] = frozenset({
    # Python (FastAPI/Flask adapters)
    "str", "int", "float", "bool", "dict",
    "list[str]", "list[int]", "list[float]", "list[bool]",
    # TypeScript (NestJS/Express adapters)
    "string", "number", "boolean", "object",
    "string[]", "number[]", "boolean[]",
})

# Streaming response content types (SSE/NDJSON) recognized during live probing.
STREAMING_CONTENT_TYPES: frozenset[str] = frozenset({
    "text/event-stream",
    "application/x-ndjson",
    "application/ndjson",
    "application/jsonl",
    "application/x-jsonlines",
})


def has_required_structured_field(
    request_body_schema: "dict[str, str] | None", payload_key: str
) -> bool:
    """Detect a required non-scalar field (other than the chat payload key).

    A field is "required" when its type hint has no ``| None`` / ``Optional[...]``
    marker. A required field of a custom/structured type (e.g.
    ``list[VisualDocumentPage]``) can't be synthesised from plain probe text, so
    the endpoint will reject a normal chat request — a strong signal this is a
    non-conversational route (document upload, image analysis, etc.) even
    though its path or payload key looks chat-like.
    """
    if not request_body_schema:
        return False
    for field_name, type_hint in request_body_schema.items():
        if field_name == payload_key or not isinstance(type_hint, str):
            continue
        normalized = type_hint.replace(" ", "")
        if "|None" in normalized or normalized.startswith("Optional["):
            continue
        if normalized in SIMPLE_SCALAR_TYPES:
            continue
        return True
    return False


def normalize_payload_key(key: str) -> str:
    """Normalize a payload key for comparison against RUNTIME_NON_CHAT_KEYS.

    Converts camelCase (e.g. "patientName") to snake_case ("patient_name")
    and lowercases, so both spellings match a single blocklist entry.
    """
    return CAMEL_CASE_RE.sub("_", key).lower()


DEFAULT_OPENAPI_TIMEOUT_SECONDS = 5.0
DEFAULT_LIVENESS_TIMEOUT_SECONDS = 10.0
DEFAULT_ENRICHMENT_TIMEOUT_SECONDS = 4.0
DEFAULT_MAX_PROBE_REQUESTS = 10
# How long a persisted runtime-probe-confirmed chat endpoint (see
# ``ProbeExtras.confirmed_at`` below) is trusted before it must be
# re-verified by a fresh live probe. Mirrors
# ``nuguard.common.endpoint_liveness.DEFAULT_LIVENESS_CACHE_TTL_SECONDS``.
DEFAULT_ENDPOINT_CONFIRMATION_TTL_SECONDS = 3600.0

# ``NodeMetadata.extras`` (nuguard.sbom.models) is a generic ``dict[str, Any]``
# shared by many unrelated adapters. These are the subset of keys written by
# SBOM auto-enrichment / live probing (nuguard.common.auto_sbom_enricher) and
# read back during SBOM-based endpoint scoring (this package's ``sbom``
# module). Both sides should go through ``ProbeExtras``/the constants below
# instead of raw string literals so a typo can't silently break scoring.
PROBE_SOURCE_AUTO_ENRICHMENT = "auto_enrichment"
PROBE_SOURCE_RUNTIME_PROBE = "runtime_probe"


class ProbeExtras(TypedDict, total=False):
    """Known probe-related keys stored in ``NodeMetadata.extras``."""

    source: str
    probe_value_template: dict[str, Any] | None
    # ISO-8601 UTC timestamp set when ``source == PROBE_SOURCE_RUNTIME_PROBE``
    # is persisted — lets a later run decide whether this confirmation is
    # still fresh enough to trust without re-probing (see
    # ``DEFAULT_ENDPOINT_CONFIRMATION_TTL_SECONDS`` and
    # ``nuguard.common.endpoint_detection.sbom.find_confirmed_chat_endpoint``).
    confirmed_at: str
    probe_get_404: bool
    probe_post_405: bool
