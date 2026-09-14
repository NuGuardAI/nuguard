"""Compatibility shim for legacy imports of ``nuguard.common.endpoint_probe``.

The real implementation has moved into ``nuguard.common.endpoint_detection``:

- SBOM-only (zero-I/O) scoring → ``endpoint_detection.sbom``
- Live HTTP/WebSocket probing → ``endpoint_detection.live_probe``
- Frontend-bundle API-origin discovery → ``endpoint_detection.frontend_origin``
- Shared constants/regexes → ``endpoint_detection.constants``

This module re-exports the same names under their original signatures so
existing callers/tests that import directly from ``nuguard.common.endpoint_probe``
continue to work unchanged. New code should import from
``nuguard.common.endpoint_detection`` instead.
"""
from __future__ import annotations

from nuguard.common.endpoint_detection.constants import (
    CHAT_TEXT_SENTINEL as _CHAT_TEXT_SENTINEL,
)
from nuguard.common.endpoint_detection.constants import (
    HAS_PATH_PARAM_RE as _HAS_PATH_PARAM_RE,
)
from nuguard.common.endpoint_detection.frontend_origin import (
    discover_api_origin_from_frontend_bundle,
)
from nuguard.common.endpoint_detection.live_probe import (
    ProbeResult,
    _looks_like_chat_response,
    compute_websocket_accept,
    is_empty_session_response,
    probe_chat_endpoints,
)
from nuguard.common.endpoint_detection.sbom import (
    _sbom_post_paths,
    discover_chat_candidates_from_sbom,
    discover_chat_config_from_sbom,
    sbom_indicates_websocket,
)

__all__ = [
    "ProbeResult",
    "_CHAT_TEXT_SENTINEL",
    "_HAS_PATH_PARAM_RE",
    "_looks_like_chat_response",
    "_sbom_post_paths",
    "compute_websocket_accept",
    "discover_api_origin_from_frontend_bundle",
    "discover_chat_candidates_from_sbom",
    "discover_chat_config_from_sbom",
    "is_empty_session_response",
    "probe_chat_endpoints",
    "sbom_indicates_websocket",
]

