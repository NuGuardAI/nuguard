"""Configuration-aware payload-shape detection."""

from __future__ import annotations

from typing import Any

from nuguard.common.endpoint_detection.constants import (
    DEFAULT_PAYLOAD_KEY,
    DEFAULT_PAYLOAD_LIST,
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    UNSET,
)
from nuguard.common.endpoint_detection.live_probe import probe_endpoint
from nuguard.common.endpoint_detection.models import EndpointSource, PayloadShape


async def detect_payload_shape(
    target_url: str,
    sbom: Any,
    endpoint: str,
    *,
    payload_key: object = UNSET,
    payload_list: object = UNSET,
    value_template: object = UNSET,
    response_key: object = UNSET,
    auth_headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    probe_payload_extras: dict[str, object] | None = None,
    llm: Any = None,
) -> PayloadShape:
    """Resolve missing payload fields for a known endpoint.

    Explicit values are constraints and are never replaced by probe results.
    Omitted values use a targeted probe against ``endpoint``. The function
    uses ``UNSET`` instead of defaults so explicit ``message`` and ``False``
    remain distinguishable from omitted configuration.
    """
    key_is_explicit = payload_key is not UNSET
    list_is_explicit = payload_list is not UNSET
    template_is_explicit = value_template is not UNSET
    response_is_explicit = response_key is not UNSET

    if key_is_explicit and list_is_explicit and template_is_explicit and response_is_explicit:
        return PayloadShape(
            key=str(payload_key),
            is_list=bool(payload_list),
            value_template=value_template,
            response_key=response_key,
            source=EndpointSource.CONFIG,
            explicit_key=True,
            explicit_list=True,
            explicit_template=True,
            notes=("Payload shape supplied by configuration.",),
        )

    result = await probe_endpoint(
        target_url,
        sbom,
        auth_headers=auth_headers,
        timeout=timeout,
        known_payload_key=str(payload_key) if key_is_explicit else None,
        known_payload_list=bool(payload_list) if list_is_explicit else False,
        known_response_key=str(response_key) if response_is_explicit else None,
        probe_payload_extras=probe_payload_extras,
        hint_path=endpoint,
        llm=llm,
    )

    detected_key = result.key if result is not None else None
    detected_list = result.is_list if result is not None else False
    detected_template = result.value_template if result is not None else None

    resolved_key = str(payload_key) if key_is_explicit else detected_key or DEFAULT_PAYLOAD_KEY
    resolved_list = bool(payload_list) if list_is_explicit else detected_list
    resolved_template = value_template if template_is_explicit else detected_template
    resolved_response = str(response_key) if response_is_explicit else None

    if result is None:
        source = EndpointSource.FALLBACK
        notes = (
            f"Payload shape probe did not resolve {endpoint!r}; defaults were retained.",
        )
    else:
        source = EndpointSource.CONFIG if key_is_explicit and list_is_explicit else EndpointSource.PROBE
        notes = (f"Payload shape inferred for {endpoint!r}.",)

    return PayloadShape(
        key=resolved_key,
        is_list=resolved_list,
        value_template=resolved_template,
        response_key=resolved_response,
        source=source,
        explicit_key=key_is_explicit,
        explicit_list=list_is_explicit,
        explicit_template=template_is_explicit,
        notes=notes,
    )
