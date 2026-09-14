"""Constants shared by endpoint and payload detection strategies."""

from __future__ import annotations


class _UnsetType:
    """Sentinel type used when a configuration field was not supplied."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _UnsetType()

DEFAULT_PAYLOAD_KEY = "message"
DEFAULT_PAYLOAD_LIST = False

HTTP_ENDPOINT_FALLBACK_PATHS = (
    "/chat",
    "/run",
    "/api/chat",
    "/v1/chat",
    "/query",
    "/agent",
)

WEBSOCKET_ENDPOINT_FALLBACK_PATHS = (
    "/ws",
    "/ws/chat",
    "/socket",
    "/realtime",
)

# These statuses indicate that a candidate path did not accept the attempted request.
ROTATION_STATUS_CODES = frozenset({400, 404, 405, 422})

# These statuses mean the target responded and therefore is not dead for liveness purposes.
REACHABLE_AUTH_STATUS_CODES = frozenset({401, 403})

DEFAULT_PROBE_TIMEOUT_SECONDS = 15.0
DEFAULT_OPENAPI_TIMEOUT_SECONDS = 5.0
DEFAULT_LIVENESS_TIMEOUT_SECONDS = 10.0
DEFAULT_ENRICHMENT_TIMEOUT_SECONDS = 4.0
DEFAULT_MAX_PROBE_REQUESTS = 10
