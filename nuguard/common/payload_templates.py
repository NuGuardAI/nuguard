"""Internal payload templating shared by chat turns and live probes."""

from __future__ import annotations

import re
import uuid
from typing import Any, cast

# Recognized tokens inside a chat_payload_extras template — see
# TargetAppClient._build_templated_chat_payload. Deliberately only these four;
# an unrecognized {{foo}} sequence is left untouched rather than guessed at.
_PAYLOAD_TOKEN_PATTERN = re.compile(r"\{\{(message|history|session_id|conversation_id)\}\}")


def contains_message_token(obj: Any) -> bool:
    """Recursively check whether any string value in *obj* contains ``{{message}}``.

    This gate decides whether chat_payload_extras is treated as a literal
    template (this feature) or merged with the auto-detected body shape
    (existing behaviour, unchanged when the token is absent).
    """
    if isinstance(obj, str):
        return "{{message}}" in obj
    if isinstance(obj, dict):
        return any(contains_message_token(v) for v in obj.values())
    if isinstance(obj, list):
        return any(contains_message_token(v) for v in obj)
    return False


def render_payload_template(
    template: Any,
    *,
    message: str,
    history: str,
    session_id: str,
    conversation_id: str,
) -> Any:
    """Deep-copy *template*, substituting ``{{message}}``/``{{history}}``/
    ``{{session_id}}``/``{{conversation_id}}`` tokens in every string value.

    Substitution happens on Python objects before ``json.dumps``, so message
    content containing quotes or braces is safe — there is no JSON-injection
    risk from user-controlled text landing in ``message``/``history``.
    """
    substitutions = {
        "message": message,
        "history": history,
        "session_id": session_id,
        "conversation_id": conversation_id,
    }

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            return _PAYLOAD_TOKEN_PATTERN.sub(lambda m: substitutions[m.group(1)], node)
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(v) for v in node]
        return node

    return _walk(template)


def build_probe_payload(
    key: str,
    value: object,
    extras: dict[str, Any] | None,
    *,
    message: str,
) -> dict[str, Any]:
    """Use a configured full-body template, or preserve the static-extras merge.

    A probe is a fresh first turn: it has no history or server conversation ID.
    Use a fresh session ID without writing rendered values back into config.
    """
    if contains_message_token(extras):
        return cast(
            dict[str, Any],
            render_payload_template(
                extras,
                message=message,
                history="[]",
                session_id=str(uuid.uuid4()),
                conversation_id="",
            ),
        )
    return {**(extras or {}), key: value}
