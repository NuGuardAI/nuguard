"""Slot-token constants for ``chat_payload_extras`` slot mode.

Kept dependency-light (no ``httpx``, no ``TargetAppClient``) so ``nuguard.config``
can import it for validation without pulling in the redteam target client's
heavier dependencies at process startup.
"""
from __future__ import annotations

import re
from typing import Any

MESSAGE = "{{message}}"
HISTORY = "{{history}}"
SESSION_ID = "{{session_id}}"
CONVERSATION_ID = "{{conversation_id}}"

# Tokens recognized inside ``chat_payload_extras`` slot mode.
EXTRAS_RECOGNIZED: frozenset[str] = frozenset({MESSAGE, HISTORY, SESSION_ID, CONVERSATION_ID})

_TOKEN_RE = re.compile(r"\{\{[^{}]*\}\}")


def contains_any(node: Any, tokens: frozenset[str]) -> bool:
    """True when *node* contains any of *tokens* as an exact string value or dict key."""
    if isinstance(node, str):
        return node in tokens
    if isinstance(node, dict):
        return any(k in tokens or contains_any(v, tokens) for k, v in node.items())
    if isinstance(node, list):
        return any(contains_any(item, tokens) for item in node)
    return False


def find_unrecognized(node: Any, recognized: frozenset[str]) -> set[str]:
    """Return every ``{{...}}``-shaped string/key in *node* not in *recognized*.

    Matches any substring of the form ``{{...}}`` (not just an exact whole-string
    token), so a typo like ``{{mesage}}`` or a token embedded in a larger string
    like ``"Hi {{message}}"`` is both caught — the latter is also invalid because
    substitution only ever matches an exact whole-string token, so a token used as
    a substring would otherwise silently pass through unresolved.
    """
    found: set[str] = set()

    def _scan_str(s: str) -> None:
        for match in _TOKEN_RE.findall(s):
            if match not in recognized:
                found.add(match)

    def _walk(n: Any) -> None:
        if isinstance(n, str):
            _scan_str(n)
        elif isinstance(n, dict):
            for k, v in n.items():
                _scan_str(k)
                _walk(v)
        elif isinstance(n, list):
            for item in n:
                _walk(item)

    _walk(node)
    return found


def max_depth(node: Any) -> int:
    """Return the max container nesting depth of *node*.

    A scalar (str, number, bool, None) is depth 0. A dict/list of scalars is
    depth 1. A dict/list containing another dict/list is depth 2+. Used to
    distinguish flat ``chat_payload_extras`` (today's sibling-field merge)
    from nested ``chat_payload_extras`` (slot mode, where the whole structure
    becomes the request body).
    """
    if isinstance(node, dict):
        if not node:
            return 1
        return 1 + max((max_depth(v) for v in node.values()), default=0)
    if isinstance(node, list):
        if not node:
            return 1
        return 1 + max((max_depth(item) for item in node), default=0)
    return 0


def substitute(node: Any, context: dict[str, Any]) -> Any:
    """Recursively replace exact-match slot tokens in *node* with values from *context*.

    Only a string value that is *exactly* one of the recognized tokens (e.g.
    ``"{{message}}"``) is replaced, and it is replaced with whatever type
    *context* holds for that token (str, list, dict, ...) — a token embedded
    in a larger string (e.g. ``"Hi {{message}}"``) is left untouched (see
    :func:`find_unrecognized`, which flags that usage as invalid at config
    time). Dict keys are never substituted, only values.

    A token that resolves to ``None`` (e.g. ``{{conversation_id}}`` before the
    target has ever issued one) has its dict key, or its list element, dropped
    entirely rather than sending a literal ``null`` — the token simply has no
    value *yet* for this turn, so the field shouldn't be sent at all. A value
    that is explicitly ``None`` in the source config (not a token) is left
    alone, since that wasn't a substitution.
    """
    if isinstance(node, str):
        return context.get(node, node)
    if isinstance(node, dict):
        result = {}
        for k, v in node.items():
            if isinstance(v, str) and v in context and context[v] is None:
                continue
            result[k] = substitute(v, context)
        return result
    if isinstance(node, list):
        return [
            substitute(item, context)
            for item in node
            if not (isinstance(item, str) and item in context and context[item] is None)
        ]
    return node
