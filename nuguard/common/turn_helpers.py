"""Mid-turn interrupt helpers shared by behavior and redteam workflows.

During a multi-turn conversation, an AI agent may interrupt the flow to ask
for credentials, request confirmation of an action, or ask a clarifying
question. Both :class:`~nuguard.behavior.runner.BehaviorRunner` and
:class:`~nuguard.redteam.executor.guided_executor.GuidedAttackExecutor` need
identical handling.  This module provides two helpers:

* :func:`handle_credential_request` — detect and supply a credential, return
  the resulting response so the caller can continue.
* :func:`handle_confirmation_request` — detect and reply to a
  confirmation/clarification ask, return the resulting response.
* :func:`handle_mid_turn_interrupts` — calls both in sequence; returns the
  (possibly updated) response and an updated list of tool calls / canary hits.

Callers that need only one of the two behaviours can call the lower-level
functions directly.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nuguard.redteam.target.client import TargetAppClient
    from nuguard.redteam.target.session import AttackSession

_log = logging.getLogger(__name__)


async def handle_credential_request(
    client: "TargetAppClient",
    session: "AttackSession",
    response: str,
    credentials: dict[str, str],
) -> tuple[str, list, bool]:
    """Detect a credential request in *response* and supply it.

    If the agent is asking for a credential *and* credentials are configured,
    sends the credential value and returns the (updated response, updated tool
    calls, login_success flag).  Otherwise returns the original values unchanged.

    Args:
        client: The :class:`~nuguard.redteam.target.client.TargetAppClient`
            to use for the follow-up send.
        session: The current :class:`~nuguard.redteam.target.session.AttackSession`.
        response: The agent's latest response text.
        credentials: Dict mapping credential type → value (from ``nuguard.yaml``).

    Returns:
        ``(response, tool_calls, login_success)`` — all updated if a credential
        was supplied, original values otherwise.
    """
    from nuguard.common.credentials import (
        credential_reply,
        detect_credential_request,
        detect_login_success,
        get_credential,
    )

    tool_calls: list = []
    if not response or not credentials:
        return response, tool_calls, False

    cred_type = detect_credential_request(response)
    if not cred_type:
        return response, tool_calls, False

    value = get_credential(cred_type, credentials)
    cred_msg = credential_reply(cred_type, value)
    _log.debug("handle_credential_request: supplying credential type=%s", cred_type)
    try:
        cred_response, cred_calls = await client.send(cred_msg, session=session)
        login_success = detect_login_success(cred_response or "")
        if cred_response:
            return cred_response, list(cred_calls or []), login_success
    except Exception as exc:
        _log.debug("handle_credential_request: credential send failed: %s", exc)
    return response, tool_calls, False


async def handle_confirmation_request(
    client: "TargetAppClient",
    session: "AttackSession",
    response: str,
    original_message: str,
    llm_client: Any | None = None,
) -> tuple[str, list]:
    """Detect a confirmation or clarification request in *response* and reply.

    If the agent is asking for confirmation or clarification, generates a
    contextual reply (or falls back to a hardcoded one) and sends it.

    Args:
        client: The :class:`~nuguard.redteam.target.client.TargetAppClient`.
        session: The current :class:`~nuguard.redteam.target.session.AttackSession`.
        response: The agent's latest response text.
        original_message: The user's original request that started this turn
            (used by the LLM to generate a specific reply).
        llm_client: Optional LLMClient for generating contextual replies.

    Returns:
        ``(response, tool_calls)`` — updated if a confirmation/clarification
        was detected, original values otherwise.
    """
    from nuguard.common.credentials import (
        detect_confirmation_request,
        generate_contextual_reply,
    )

    if not response:
        return response, []

    confirm_type = detect_confirmation_request(response)
    if not confirm_type:
        return response, []

    try:
        confirm_msg = await generate_contextual_reply(
            agent_response=response,
            original_message=original_message,
            llm_client=llm_client,
        )
        _log.debug(
            "handle_confirmation_request: auto-replying to %s request: %s",
            confirm_type,
            confirm_msg[:80],
        )
        confirmed_response, confirmed_calls = await client.send(confirm_msg, session=session)
        if confirmed_response:
            return confirmed_response, list(confirmed_calls or [])
    except Exception as exc:
        _log.debug("handle_confirmation_request: confirmation send failed: %s", exc)

    return response, []


async def handle_mid_turn_interrupts(
    client: "TargetAppClient",
    session: "AttackSession",
    response: str,
    original_message: str,
    tool_calls: list | None = None,
    credentials: dict[str, str] | None = None,
    llm_client: Any | None = None,
) -> tuple[str, list]:
    """Handle all mid-turn agent interrupts in one call.

    Applies credential auto-supply first, then confirmation/clarification
    handling. Each step uses the (possibly updated) response from the
    previous step.

    Args:
        client: The :class:`~nuguard.redteam.target.client.TargetAppClient`.
        session: The current :class:`~nuguard.redteam.target.session.AttackSession`.
        response: The agent's latest response text.
        original_message: The task message that started the current turn.
        tool_calls: Tool calls returned with *response* (may be extended by
            follow-up sends).
        credentials: Configured credentials dict.  Pass ``None`` or ``{}`` to
            skip credential handling.
        llm_client: Optional LLMClient for contextual confirmation replies.

    Returns:
        The (possibly updated) ``(response, tool_calls)`` tuple.
    """
    accumulated_calls: list = list(tool_calls or [])

    # ── 1. Credential auto-supply ────────────────────────────────────────────
    if credentials:
        updated_response, cred_calls, _login_ok = await handle_credential_request(
            client=client,
            session=session,
            response=response,
            credentials=credentials,
        )
        if updated_response != response:
            response = updated_response
            accumulated_calls = accumulated_calls + cred_calls

    # ── 2. Confirmation / clarification reply ────────────────────────────────
    updated_response, confirm_calls = await handle_confirmation_request(
        client=client,
        session=session,
        response=response,
        original_message=original_message,
        llm_client=llm_client,
    )
    if updated_response != response:
        response = updated_response
        accumulated_calls = accumulated_calls + confirm_calls

    return response, accumulated_calls
