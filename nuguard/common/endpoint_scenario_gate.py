"""Shared decision for skipping direct-HTTP attack/coverage scenarios against
an ``API_ENDPOINT`` node.

Both :mod:`nuguard.redteam.scenarios.generator` and
:mod:`nuguard.behavior.scenarios` independently decide whether to synthesize
a direct-HTTP scenario for a given SBOM node. Extracting the decision here
means a future fix to it (e.g. a new "definitely not attackable" signal)
never has to be manually ported between the two packages again.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from nuguard.common.endpoint_liveness import _looks_like_rest_path

if TYPE_CHECKING:
    from nuguard.sbom.models import NodeMetadata


def should_skip_direct_http_scenario(
    meta: "NodeMetadata", *, endpoint: str | None = None
) -> tuple[bool, str | None]:
    """Return ``(skip, reason)`` for whether a direct-HTTP scenario should be
    generated against the endpoint described by *meta*.

    Skips when the endpoint value is structurally not an HTTP path (bind
    address, MCP/SSE annotation, ...) or when a prior live liveness probe
    (:func:`~nuguard.common.endpoint_liveness.check_endpoint_liveness`)
    confirmed the endpoint is dead (``operational is False``). An endpoint
    that was never probed (``operational is None``) is NOT skipped —
    absence of a probe result must never suppress scenario generation.

    *endpoint* overrides ``meta.endpoint`` for the structural check only —
    callers that resolve a fallback path (e.g. from the node's name) when
    ``meta.endpoint`` is unset should pass that resolved path here so the
    structural check applies to the path actually used, not the empty
    ``meta.endpoint`` that triggered the fallback.
    """
    endpoint_value = endpoint if endpoint is not None else meta.endpoint
    if not _looks_like_rest_path(endpoint_value):
        return True, (
            f"endpoint metadata {endpoint_value!r} is not an HTTP path "
            f"(bind address / MCP-SSE annotation) — no REST route to attack directly."
        )
    if meta.operational is False:
        return True, (
            f"endpoint {endpoint_value!r} was confirmed non-operational by a live "
            f"liveness probe — no live REST route to attack directly."
        )
    return False, None
