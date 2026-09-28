"""Resolve path-param placeholders in a templated chat endpoint (two-step chat).

Some chat APIs model conversations as create-then-post-to-subresource:

    POST /chat/conversations              -> {"id": "c_123", ...}
    POST /chat/conversations/:id/messages {"content": "..."}

The SBOM enricher records, per templated endpoint, which collection endpoint
creates each placeholder's resource (``metadata.path_param_sources``).
:func:`resolve_path_param_values` performs those prerequisite POSTs and
returns the ids to substitute. It is transport-agnostic: callers pass a
``post(path, body) -> (status, parsed_json)`` coroutine, so the same logic
serves the endpoint pre-flight (via ``TargetAppClient.invoke_endpoint``)
and the auth bootstrap health check (via a raw httpx client).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from nuguard.common.logging import get_logger
from nuguard.common.response_extraction import build_minimal_payload, extract_response_id

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)

PostFn = Callable[[str, dict], Awaitable["tuple[int, Any]"]]

_COLON_PARAM_RE = re.compile(r":([A-Za-z_]\w*)")
_BRACE_PARAM_RE = re.compile(r"\{([A-Za-z_]\w*)\}")
_ANGLE_PARAM_RE = re.compile(r"<(?:\w+:)?([A-Za-z_]\w*)>")


def substitute_path_params(path: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """Substitute ``:name`` / ``{name}`` / ``<name>`` placeholders in *path*.

    Returns ``(resolved_path, missing_names)``; unresolved placeholders are
    left untouched.

    Example:
        >>> substitute_path_params("/c/:id/messages", {"id": "c1"})
        ('/c/c1/messages', [])
    """
    missing: list[str] = []

    def _repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in values:
            return values[name]
        missing.append(name)
        return m.group(0)

    # Angle first: Flask's "<int:pk>" would otherwise match the colon form.
    resolved = _ANGLE_PARAM_RE.sub(_repl, path)
    resolved = _BRACE_PARAM_RE.sub(_repl, resolved)
    resolved = _COLON_PARAM_RE.sub(_repl, resolved)
    return resolved, missing


async def resolve_path_param_values(
    post: PostFn,
    sbom: "AiSbomDocument",
    chat_path: str,
) -> dict[str, str]:
    """Create each prerequisite resource for *chat_path* and return ``{param: id}``.

    Reads ``path_param_sources`` off the SBOM ``API_ENDPOINT`` node whose
    endpoint equals *chat_path*. For each param with a known source it POSTs
    ``{}`` to the source endpoint, retrying once with a minimal payload built
    from the source's ``request_body_schema`` on a 4xx/5xx, then extracts the
    created resource's id. Best-effort: params that can't be resolved are
    simply omitted, and *post* exceptions are logged, not raised.
    """
    from nuguard.sbom.types import ComponentType  # noqa: PLC0415

    chat_node = next(
        (n for n in sbom.nodes if n.metadata and (n.metadata.endpoint or "") == chat_path),
        None,
    )
    if chat_node is None or chat_node.metadata is None:
        return {}
    sources = chat_node.metadata.path_param_sources or {}
    if not sources:
        return {}

    endpoints_by_path = {
        n.metadata.endpoint: n
        for n in sbom.nodes
        if n.component_type == ComponentType.API_ENDPOINT and n.metadata and n.metadata.endpoint
    }

    values: dict[str, str] = {}
    # Path-param order, so an outer resource id is available before an inner one.
    for param in [p for p in (chat_node.metadata.path_params or []) if p in sources]:
        source_path = sources[param]
        try:
            status, data = await post(source_path, {})
            if status >= 400:
                source_node = endpoints_by_path.get(source_path)
                schema = (
                    source_node.metadata.request_body_schema
                    if source_node and source_node.metadata
                    else None
                ) or {}
                if schema:
                    status, data = await post(source_path, build_minimal_payload(schema))
        except Exception as exc:  # noqa: BLE001 - best effort
            from nuguard.common.errors import TargetQuotaExhaustedError  # noqa: PLC0415

            if isinstance(exc, TargetQuotaExhaustedError):
                raise
            _log.info(
                "path-param bootstrap POST %s raised (non-fatal): %s — leaving %r unbound",
                source_path, exc, param,
            )
            continue
        if status >= 400:
            _log.info(
                "path-param bootstrap POST %s failed (HTTP %d) — leaving %r unbound",
                source_path, status, param,
            )
            continue
        resolved_id = extract_response_id(data, extra_keys=("id",))
        if not resolved_id:
            _log.info(
                "path-param bootstrap POST %s succeeded but no id found in response — "
                "leaving %r unbound",
                source_path, param,
            )
            continue
        values[param] = resolved_id
    return values
