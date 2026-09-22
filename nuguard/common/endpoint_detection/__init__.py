"""Endpoint path and payload-shape detection.

This package will become the single public entry point for resolving a target's
chat endpoint and the payload shape required by that endpoint.
"""

from nuguard.common.endpoint_detection.constants import UNSET
from nuguard.common.endpoint_detection.models import (
    EndpointSource,
    FieldResolution,
    PayloadShape,
    ResolvedEndpoint,
)


def __getattr__(name: str):
    """Load probe-dependent APIs only when a caller requests them."""
    if name == "discover_chat_candidates":
        from nuguard.common.endpoint_detection.sbom import discover_chat_candidates

        return discover_chat_candidates
    if name == "discover_chat_config":
        from nuguard.common.endpoint_detection.sbom import discover_chat_config

        return discover_chat_config
    if name == "indicates_websocket":
        from nuguard.common.endpoint_detection.sbom import indicates_websocket

        return indicates_websocket
    if name == "probe_endpoint":
        from nuguard.common.endpoint_detection.live_probe import probe_endpoint

        return probe_endpoint
    if name == "probe_payload_shape":
        from nuguard.common.endpoint_detection.live_probe import probe_payload_shape

        return probe_payload_shape
    if name == "detect_payload_shape":
        from nuguard.common.endpoint_detection.payload import detect_payload_shape

        return detect_payload_shape
    if name == "payload_shape_from_probe_result":
        from nuguard.common.endpoint_detection.payload import payload_shape_from_probe_result

        return payload_shape_from_probe_result
    if name == "normalize_probe_result":
        from nuguard.common.endpoint_detection.live_probe import normalize_probe_result

        return normalize_probe_result
    if name == "validate_and_rotate":
        from nuguard.common.endpoint_detection.rotation import validate_and_rotate

        return validate_and_rotate
    if name == "detect_with_browser":
        from nuguard.common.endpoint_detection.browser import detect_with_browser

        return detect_with_browser
    if name == "resolve_chat_endpoint":
        from nuguard.common.endpoint_detection.resolver import resolve_chat_endpoint

        return resolve_chat_endpoint
    if name == "find_confirmed_chat_endpoint":
        from nuguard.common.endpoint_detection.sbom import find_confirmed_chat_endpoint

        return find_confirmed_chat_endpoint
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "UNSET",
    "EndpointSource",
    "FieldResolution",
    "PayloadShape",
    "ResolvedEndpoint",
    "discover_chat_candidates",
    "discover_chat_config",
    "indicates_websocket",
    "probe_endpoint",
    "probe_payload_shape",
    "detect_payload_shape",
    "payload_shape_from_probe_result",
    "normalize_probe_result",
    "validate_and_rotate",
    "detect_with_browser",
    "resolve_chat_endpoint",
    "find_confirmed_chat_endpoint",
]
