"""Conversation-state capabilities of a target, and a behavioural isolation check.

Campaign mode only shares/reuses a conversation branch when the target's state
model makes that safe, and only claims fresh-session isolation after a
behavioural verification (a second fresh branch must not see the first
branch's marker).
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable

from nuguard.redteam.target.client import _contains_message_token, _is_message_history_key


class SessionMode(str, Enum):
    """Where conversation state lives."""

    STATELESS = "stateless"            # every request independent
    CLIENT_HISTORY = "client_history"  # client replays history each turn
    SERVER_SESSION = "server_session"  # server keeps a session/conversation id


@dataclass(frozen=True)
class TargetCapabilities:
    """What campaign mode may assume about the target's conversation state."""

    mode: SessionMode
    supports_reset: bool            # a fresh, isolated conversation can be created
    parallel_branches: bool         # independent branches may run concurrently
    source: str                     # "adapter" | "payload_config" | "transport" | "operator"
    isolation_verified: bool | None = None  # None = not (yet) verified / inconclusive

    def with_operator_reset_claim(self, claim: bool | None) -> "TargetCapabilities":
        """Apply ``redteam.campaign.target_supports_session_reset`` (None = keep detected)."""
        if claim is None:
            return self
        return TargetCapabilities(
            self.mode, claim, self.parallel_branches, "operator", self.isolation_verified
        )


def classify_target(client: Any) -> TargetCapabilities:
    """Classify a target client's conversation-state model.

    Order: framework-adapter declaration, then transport kind (WebSocket is a
    single persistent socket → no parallel branches), then payload config
    (history key or ``{{history}}`` → client history; session tokens or a
    templated chat path → server session), else stateless.
    """
    if type(client).__name__ == "WebSocketTargetClient":
        return TargetCapabilities(
            SessionMode.SERVER_SESSION, supports_reset=False, parallel_branches=False,
            source="transport",
        )

    adapter = getattr(client, "_framework_adapter", None)
    declared = getattr(adapter, "session_mode", None) if adapter is not None else None
    if declared is not None:
        return TargetCapabilities(
            SessionMode(str(declared)),
            supports_reset=bool(getattr(adapter, "supports_reset", False)),
            parallel_branches=True,
            source="adapter",
        )

    extras = getattr(client, "_chat_payload_extras", None) or {}
    extras_text = str(extras)
    if _contains_message_token(extras) and "{{history}}" in extras_text:
        return TargetCapabilities(SessionMode.CLIENT_HISTORY, True, True, "payload_config")
    if "{{session_id}}" in extras_text or "{{conversation_id}}" in extras_text:
        return TargetCapabilities(SessionMode.SERVER_SESSION, True, True, "payload_config")
    chat_path = str(getattr(client, "_chat_path", "") or "")
    if "{" in chat_path:
        return TargetCapabilities(SessionMode.SERVER_SESSION, True, True, "payload_config")
    if getattr(client, "_chat_payload_list", False) and _is_message_history_key(
        str(getattr(client, "_chat_payload_key", ""))
    ):
        return TargetCapabilities(SessionMode.CLIENT_HISTORY, True, True, "payload_config")
    return TargetCapabilities(SessionMode.STATELESS, True, True, "payload_config")


async def reset_branch_session(client: Any, branch_id: str) -> None:
    """Drop server-side state for *branch_id* so its next send starts fresh.

    Adapter-backed targets discard the adapter's cached session for the key;
    generic targets need nothing beyond a new ``BranchTransport`` (its
    ``session_context`` is the only place the server's ids live).
    """
    adapter = getattr(client, "_framework_adapter", None)
    reset = getattr(adapter, "reset_session", None)
    if callable(reset):
        reset(branch_id)


SendFn = Callable[[str], Awaitable[str]]


async def verify_isolation(
    send_a: SendFn, send_b: SendFn, marker: str | None = None
) -> bool | None:
    """Behaviourally verify that a fresh branch does not see another branch's state.

    ``send_a`` / ``send_b`` send one turn on two *independent fresh branches*
    and return the reply text. Branch A plants a harmless marker and is asked to
    recall it (positive control). Branch B is then asked the same recall
    question.

    Returns:
        ``True``  — A recalled the marker, B did not (isolated).
        ``False`` — B returned the marker (state leaked across branches).
        ``None``  — inconclusive: A could not recall it either (stateless or
        uncooperative target), so absence in B proves nothing.
    """
    code = marker or f"NGX-{secrets.token_hex(4).upper()}"
    await send_a(f"For this conversation, remember the harmless code word {code}.")
    recall = "What code word did I ask you to remember earlier in this conversation?"
    a_reply = await send_a(recall)
    if code not in a_reply:
        return None
    b_reply = await send_b(recall)
    return code not in b_reply
