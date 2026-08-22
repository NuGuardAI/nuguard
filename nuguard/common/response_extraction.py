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
