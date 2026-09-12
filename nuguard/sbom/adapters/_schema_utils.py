"""Shared schema-shaping helpers reused across typed-backend adapters
(NestJS, FastAPI, ASP.NET Core, ...) that resolve DTO/model field maps.
"""
from __future__ import annotations

import re

_ARRAY_OR_GENERIC_SUFFIX_RE = re.compile(r"\[\]$")


def bare_type_name(type_str: str) -> str:
    """Strip a trailing array marker (``T[]`` -> ``T``) from a type string."""
    return _ARRAY_OR_GENERIC_SUFFIX_RE.sub("", (type_str or "")).strip()


def flatten_one_level(
    schema: dict[str, str], model_map: dict[str, dict[str, str]]
) -> dict[str, str]:
    """Expand fields whose type resolves to a known DTO/model into dotted keys.

    For each ``field: type`` pair in *schema* where ``type`` (after stripping
    an array marker) is itself a key in *model_map*, adds
    ``f"{field}.{nested_field}": nested_type`` for every field of the nested
    model — e.g. ``{"tokens": "TokensDto"}`` with
    ``model_map["TokensDto"] == {"accessToken": "string"}`` becomes
    ``{"tokens": "TokensDto", "tokens.accessToken": "string"}``.

    The original unflattened entry is kept alongside the expansion, since
    other consumers (e.g. chat-payload-key inference) may still want the
    bare key. Only recurses one level deep — deeper nesting is left as-is.
    """
    if not schema or not model_map:
        return dict(schema)

    flattened = dict(schema)
    for field, type_str in schema.items():
        nested_type = bare_type_name(type_str)
        nested_fields = model_map.get(nested_type)
        if not nested_fields:
            continue
        for nested_field, nested_field_type in nested_fields.items():
            flattened[f"{field}.{nested_field}"] = nested_field_type
    return flattened
