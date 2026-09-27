"""Shared JSON-response/request-body heuristics used across the target-client,
API-attack scenario builders, and endpoint-preflight bootstrap.

Both heuristics originally lived as private, single-caller implementations
(one inline in :class:`~nuguard.redteam.target.client.TargetAppClient`, one
in :mod:`nuguard.redteam.scenarios.api_attacks`) until the multi-step chat
bootstrap in :mod:`nuguard.common.endpoint_preflight` needed the same logic
but couldn't import either — ``nuguard.common`` is a lower layer than
``nuguard.redteam``. Both are consolidated here.
"""
from __future__ import annotations

# Common key names APIs use for a created/tracked resource's identifier.
SESSION_ID_KEYS: tuple[str, ...] = ("session_id", "conversation_id", "thread_id", "chat_id")


def extract_response_id(data: object, extra_keys: tuple[str, ...] = ()) -> str | None:
    """Return the first recognized identifier found in a JSON response body.

    Checks :data:`SESSION_ID_KEYS` followed by *extra_keys* (e.g. a bare
    ``"id"``, common for REST resource-creation responses). Returns ``None``
    when *data* isn't a dict or no known key is present/non-null.
    """
    if not isinstance(data, dict):
        return None
    for key in (*SESSION_ID_KEYS, *extra_keys):
        if key in data and data[key] is not None:
            return str(data[key])
    return None


# Field-name substring -> plausible value (checked in order, longest/most-specific first)
_FIELD_VALUE_HINTS: list[tuple[str, object]] = [
    ("message", "Hello, can you help me with my account?"),
    ("content", "Hello, can you help me?"),
    ("prompt", "What is my account balance?"),
    ("text", "test message"),
    ("username", "testuser@example.com"),
    ("email", "testuser@example.com"),
    ("password", "TestPass123!"),
    ("session_id", "sess-test-12345"),
    ("session", "sess-test-12345"),
    ("user_id", "user-test-001"),
    ("user", "user-test-001"),
    ("account", "acct-test-001"),
    ("tenant", "tenant-test-001"),
    ("name", "Test User"),
    ("amount", 100),
    ("price", 100),
    ("query", "show me my account details"),
]

# Type-string substring -> fallback value when no field-name hint matches
_TYPE_VALUE_FALLBACKS: list[tuple[str, object]] = [
    ("int", 1),
    ("float", 1.0),
    ("bool", True),
    ("list", []),
    ("dict", {}),
]


def build_minimal_payload(schema: dict[str, str]) -> dict:
    """Return a plausible request body dict from a ``{field_name: type_string}`` schema.

    Field-name heuristics take priority; type-string fallback applies otherwise.
    Empty schema returns an empty dict.
    """
    body: dict = {}
    for field, type_str in schema.items():
        field_lower = field.lower()
        value: object = None
        for hint_key, hint_val in _FIELD_VALUE_HINTS:
            if hint_key in field_lower:
                value = hint_val
                break
        if value is None:
            type_lower = (type_str or "str").lower()
            for type_key, type_val in _TYPE_VALUE_FALLBACKS:
                if type_key in type_lower:
                    value = type_val
                    break
            else:
                value = "test-value"
        body[field] = value
    return body


# Transport/config error markers TargetAppClient.send() returns instead of a
# reply body (see nuguard/redteam/target/client.py).
_CLIENT_ERROR_PREFIXES: tuple[str, ...] = ("[HTTP ", "[CONFIG_ERROR", "[REQUEST_ERROR", "[TIMEOUT")

# chat_fitness() score bands.
CHAT_FITNESS_NONE = 0.0
CHAT_FITNESS_STRUCTURED = 0.3
CHAT_FITNESS_TERSE = 0.6
CHAT_FITNESS_PROSE = 1.0


def _json_container_size(data: object, depth: int = 0) -> tuple[int, int]:
    """Return ``(node_count, max_depth)`` of a parsed JSON value (capped walk)."""
    if depth > 6:
        return 1, depth
    if isinstance(data, dict):
        items: list[object] = list(data.values())
    elif isinstance(data, list):
        items = list(data)
    else:
        return 1, depth
    nodes, max_depth = 1, depth
    for item in items[:50]:
        n, d = _json_container_size(item, depth + 1)
        nodes += n
        max_depth = max(max_depth, d)
    return nodes, max_depth


def _is_structured_artefact(data: object) -> bool:
    """True for a nested JSON body (a generated plan/quiz/record), not a chat envelope.

    Chat replies are shallow envelopes (``{"id", "role", "content", ...}``,
    possibly with a short ``sources`` list); generator endpoints return deep,
    wide trees such as ``{"modules": [{"steps": [...]}, ...]}``.
    """
    if not isinstance(data, (dict, list)):
        return False
    nodes, depth = _json_container_size(data)
    return depth >= 3 or nodes > 25


def chat_fitness(response: str, raw: object = None) -> float:
    """Score how much *response* looks like a conversational chat reply.

    Used by the endpoint pre-flight to choose between candidate endpoints that
    all return 2xx: a chat endpoint answers a greeting with free text, while a
    one-shot generator endpoint (e.g. a learning-path or quiz builder) returns
    a structured artefact that merely happens to be valid JSON.

    Returns one of:

    * ``0.0`` — unusable: empty, or a client error marker (``[HTTP 500]``,
      ``[CONFIG_ERROR: ...]``, ...).
    * ``0.3`` — a structured JSON object/array rather than a text reply.
    * ``0.6`` — short text (fewer than 3 words).
    * ``1.0`` — a prose reply.

    *raw* is the parsed response body when available (``TargetAppClient.last_raw_response``).
    A structured artefact body scores ``0.3`` even if a prose field was extracted
    from it (e.g. a generated plan's ``description``).

    Example:
        >>> chat_fitness("Hi! How can I help you study today?")
        1.0
        >>> chat_fitness('{"modules": [{"title": "Intro", "steps": [1, 2]}]}')
        0.3
    """
    import json  # noqa: PLC0415

    text = (response or "").strip()
    if not text or text.startswith(_CLIENT_ERROR_PREFIXES):
        return CHAT_FITNESS_NONE
    if _is_structured_artefact(raw):
        return CHAT_FITNESS_STRUCTURED
    if text[0] in "{[":
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, (dict, list)):
            nodes, depth = _json_container_size(data)
            # A small flat envelope (e.g. {"reply": "..."}) that leaked through
            # unextracted still counts as a reply; anything nested is an artefact.
            if depth <= 1 and nodes <= 4:
                return CHAT_FITNESS_TERSE
            return CHAT_FITNESS_STRUCTURED
    if len(text.split()) < 3:
        return CHAT_FITNESS_TERSE
    return CHAT_FITNESS_PROSE
