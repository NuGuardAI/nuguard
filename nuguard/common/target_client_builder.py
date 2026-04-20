"""Shared factory for building a :class:`TargetAppClient`.

Both :class:`~nuguard.behavior.runner.BehaviorRunner` and
:class:`~nuguard.redteam.executor.orchestrator.RedteamOrchestrator` need to
construct a ``TargetAppClient`` with the same config-resolution logic:

1. Detect a framework adapter (e.g. Google ADK) from the SBOM.
2. Auto-discover chat endpoint / payload shape from SBOM API_ENDPOINT nodes,
   but only for values the caller has *not* explicitly set.
3. Apply a three-tier response-key priority:
   explicit config > SBOM-discovered > ``None``.
4. Build and return a ``TargetAppClient``.

Callers that need auth should resolve their auth headers first and pass them in
via ``auth_headers``.  This module does not perform authentication.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nuguard.redteam.target.client import TargetAppClient
    from nuguard.sbom.models import AiSbomDocument

_log = logging.getLogger(__name__)


def build_target_app_client(
    target_url: str,
    *,
    endpoint: str = "",
    payload_key: str = "message",
    payload_list: bool = False,
    payload_format: str = "json",
    response_key: str | None = None,
    timeout: float = 60.0,
    auth_headers: dict[str, str] | None = None,
    sbom: "AiSbomDocument | None" = None,
    adk_cfg: Any = None,
    explicitly_set: frozenset[str] | set[str] = frozenset(),
) -> "TargetAppClient":
    """Build a :class:`TargetAppClient` with SBOM-assisted config resolution.

    Args:
        target_url: Base URL of the target application.
        endpoint: Chat endpoint path (e.g. ``"/chat"``).  Empty string means
            not yet determined — SBOM discovery and framework adapters may fill
            it in.
        payload_key: JSON body key for the chat message (default ``"message"``).
        payload_list: Whether to wrap the payload value in a list.
        payload_format: ``"json"`` or ``"form"`` encoding for the POST body.
        response_key: Explicit top-level key to extract from the JSON response.
        timeout: HTTP request timeout in seconds.
        auth_headers: Headers to include on every request (e.g. auth token).
        sbom: Optional AI-SBOM for framework detection and endpoint discovery.
        adk_cfg: Optional ADK deployment config forwarded to the framework adapter
            factory.
        explicitly_set: Set of config field names that were *explicitly* provided
            by the user (e.g. Pydantic ``model_fields_set``).  Discovery only
            overrides values that are *not* in this set.

    Returns:
        A fully configured :class:`TargetAppClient` instance.
    """
    from nuguard.redteam.target.client import TargetAppClient

    # ── 1. Framework adapter ────────────────────────────────────────────────
    framework_adapter = None
    if sbom is not None:
        try:
            from nuguard.redteam.target.framework_adapters.factory import make_framework_adapter
            framework_adapter = make_framework_adapter(sbom, adk_cfg)
            if framework_adapter is not None and not endpoint:
                endpoint = framework_adapter.run_path
                _log.info(
                    "build_target_app_client: framework adapter detected — using endpoint %s",
                    endpoint,
                )
        except Exception as exc:
            _log.debug("build_target_app_client: framework adapter detection failed: %s", exc)

    # ── 2. SBOM-based endpoint / payload discovery ──────────────────────────
    config_has_explicit_endpoint = bool(endpoint) and ("target_endpoint" in explicitly_set)
    config_has_explicit_payload = "chat_payload_key" in explicitly_set
    config_has_explicit_response_key = "chat_response_key" in explicitly_set

    discovered_response_key: str | None = None
    if sbom is not None and (not config_has_explicit_endpoint or not config_has_explicit_payload):
        try:
            from nuguard.redteam.executor.orchestrator import _discover_chat_config
            discovered_path, discovered_key, discovered_list, discovered_response_key = (
                _discover_chat_config(
                    sbom,
                    chat_path=endpoint or "/chat",
                    chat_payload_key=payload_key,
                    chat_payload_list=payload_list,
                )
            )
            if not config_has_explicit_endpoint and discovered_path not in ("/chat", endpoint):
                _log.info(
                    "build_target_app_client: SBOM-discovered endpoint %s (was %s)",
                    discovered_path,
                    endpoint or "/chat",
                )
                endpoint = discovered_path
            if not config_has_explicit_payload and discovered_key != payload_key:
                _log.info(
                    "build_target_app_client: SBOM-discovered payload key %s (was %s)",
                    discovered_key,
                    payload_key,
                )
                payload_key = discovered_key
                payload_list = discovered_list
        except Exception as exc:
            _log.debug("build_target_app_client: SBOM chat config discovery failed: %s", exc)

    # ── 3. Three-tier response key: explicit > SBOM-discovered > None ───────
    if not config_has_explicit_response_key and discovered_response_key:
        _log.info(
            "build_target_app_client: using SBOM-discovered response key %s",
            discovered_response_key,
        )
        response_key = discovered_response_key

    # ── 4. Construct the client ─────────────────────────────────────────────
    return TargetAppClient(
        base_url=target_url,
        chat_path=endpoint or "/chat",
        timeout=timeout,
        default_headers=auth_headers or None,
        chat_payload_key=payload_key,
        chat_payload_list=payload_list,
        chat_payload_format=payload_format,
        chat_response_key=response_key,
        framework_adapter=framework_adapter,
    )
