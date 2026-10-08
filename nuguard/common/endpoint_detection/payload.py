"""Configuration-aware payload-shape detection."""

from __future__ import annotations

from typing import Any, Callable, cast

from nuguard.common.endpoint_detection.constants import (
    CHAT_CONTRACT_VERSION,
    DEFAULT_PAYLOAD_KEY,
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    PROBE_SOURCE_RUNTIME_PROBE,
    UNSET,
)
from nuguard.common.endpoint_detection.live_probe import ProbeResult, probe_endpoint
from nuguard.common.endpoint_detection.models import EndpointSource, PayloadShape


def payload_shape_from_probe_result(
    result: ProbeResult | None,
    *,
    payload_key: object = UNSET,
    payload_list: object = UNSET,
    value_template: object = UNSET,
    response_key: object = UNSET,
    source: EndpointSource = EndpointSource.PROBE,
    note: str | None = None,
) -> PayloadShape:
    """Normalize one probe result while preserving explicitly supplied fields."""
    key_is_explicit = payload_key is not UNSET
    list_is_explicit = payload_list is not UNSET
    template_is_explicit = value_template is not UNSET
    response_is_explicit = response_key is not UNSET

    detected_key = result.key if result is not None else None
    detected_list = result.is_list if result is not None else False
    detected_template = result.value_template if result is not None else None

    resolved_key = str(payload_key) if key_is_explicit else detected_key or DEFAULT_PAYLOAD_KEY
    resolved_list = bool(payload_list) if list_is_explicit else detected_list
    resolved_template = value_template if template_is_explicit else detected_template
    resolved_response = (
        str(response_key)
        if response_is_explicit and response_key is not None
        else (None if response_is_explicit or result is None else result.response_key)
    )

    if result is None or not result.confirmed:
        resolved_source = EndpointSource.FALLBACK
        notes = ("The message field could not be confirmed; candidate defaults are not a validated contract.",)
    else:
        resolved_source = source
        notes = (note or "Payload shape normalized from probe result.",)

    return PayloadShape(
        key=resolved_key,
        is_list=resolved_list,
        value_template=cast(dict[str, Any] | None, resolved_template),
        response_key=resolved_response,
        source=resolved_source,
        explicit_key=key_is_explicit,
        explicit_list=list_is_explicit,
        explicit_template=template_is_explicit,
        notes=notes,
    )


def _sbom_payload_contract(sbom: Any, endpoint: str) -> tuple[str | None, bool, str | None]:
    """Read a declared request field without trusting obsolete probe metadata."""
    for node in getattr(sbom, "nodes", ()):
        metadata = node.metadata
        if (
            metadata is not None and metadata.endpoint == endpoint and metadata.chat_payload_key
            and (metadata.method or "POST").upper() in {"POST", "ANY"}
        ):
            if (metadata.extras or {}).get("source") == PROBE_SOURCE_RUNTIME_PROBE:
                if (metadata.extras or {}).get("chat_contract_version") != CHAT_CONTRACT_VERSION:
                    continue
            return metadata.chat_payload_key, bool(metadata.chat_payload_list), metadata.response_text_key
    return None, False, None


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
    probe_result_callback: Callable[[ProbeResult], None] | None = None,
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
            value_template=cast(dict[str, Any] | None, value_template),
            response_key=(
                str(response_key) if response_key is not None else None
            ),
            source=EndpointSource.CONFIG,
            explicit_key=True,
            explicit_list=True,
            explicit_template=True,
            notes=("Payload shape supplied by configuration.",),
        )

    # A matching SBOM node declares a field candidate. Preserve it when the
    # caller omitted that field instead of restarting a blind naming sweep.
    sbom_key, sbom_list, sbom_response = _sbom_payload_contract(sbom, endpoint)
    effective_key = str(payload_key) if key_is_explicit else sbom_key
    effective_response = str(response_key) if response_is_explicit and response_key is not None else (
        None if response_is_explicit else sbom_response
    )
    result = await probe_endpoint(
        target_url,
        sbom,
        auth_headers=auth_headers,
        timeout=timeout,
        known_payload_key=effective_key,
        known_payload_list=bool(payload_list) if list_is_explicit else sbom_list,
        known_response_key=effective_response,
        probe_payload_extras=probe_payload_extras,
        hint_path=endpoint,
        llm=llm,
    )
    if result is not None:
        result.response_key = effective_response
    if result is not None and result.confirmed and probe_result_callback is not None:
        probe_result_callback(result)

    return payload_shape_from_probe_result(
        result,
        payload_key=payload_key,
        payload_list=payload_list,
        value_template=value_template,
        response_key=response_key,
        source=(
            EndpointSource.CONFIG
            if key_is_explicit and list_is_explicit
            else EndpointSource.PROBE
        ),
        note=f"Payload shape inferred for {endpoint!r}." if result is not None else None,
    )
