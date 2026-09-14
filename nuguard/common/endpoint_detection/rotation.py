"""Endpoint verification and rotation adapters."""

from __future__ import annotations

from typing import Any

from nuguard.common.endpoint_detection.constants import ROTATION_STATUS_CODES
from nuguard.common.endpoint_preflight import PreflightOutcome
from nuguard.common.endpoint_preflight import (
    validate_and_rotate_chat_endpoint as _validate_and_rotate,
)


def response_indicates_wrong_endpoint(response: str, status_code: int | None = None) -> bool:
    """Return whether a response should trigger endpoint rotation."""
    if status_code is not None:
        return status_code in ROTATION_STATUS_CODES
    return response.startswith(tuple(f"[HTTP {code}]" for code in ROTATION_STATUS_CODES)) or not response.strip()


async def validate_and_rotate(
    client: Any,
    sbom: Any,
    *,
    has_explicit_endpoint: bool,
    target_url: str = "",
    auth_headers: dict[str, str] | None = None,
) -> PreflightOutcome:
    """Validate the current route and rotate through existing candidates when allowed."""
    return await _validate_and_rotate(
        client,
        sbom,
        has_explicit_endpoint=has_explicit_endpoint,
        target_url=target_url,
        auth_headers=auth_headers,
    )
