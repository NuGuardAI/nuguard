"""Configuration-aware endpoint and payload resolution orchestrator."""

from __future__ import annotations

from typing import Any, Callable, cast

from nuguard.common.endpoint_detection.browser import detect_with_browser
from nuguard.common.endpoint_detection.constants import (
    DEFAULT_PAYLOAD_KEY,
    DEFAULT_PAYLOAD_LIST,
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    UNSET,
)
from nuguard.common.endpoint_detection.live_probe import ProbeResult, probe_endpoint
from nuguard.common.endpoint_detection.models import (
    EndpointSource,
    PayloadShape,
    ResolvedEndpoint,
)
from nuguard.common.endpoint_detection.payload import (
    detect_payload_shape,
    payload_shape_from_probe_result,
)
from nuguard.common.endpoint_detection.sbom import discover_chat_config, indicates_websocket


async def resolve_chat_endpoint(
    target_url: str,
    sbom: Any,
    *,
    endpoint: object = UNSET,
    payload_key: object = UNSET,
    payload_list: object = UNSET,
    value_template: object = UNSET,
    response_key: object = UNSET,
    auth_headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    probe_payload_extras: dict[str, object] | None = None,
    llm: Any = None,
    enable_browser_fallback: bool = False,
    probe_result_callback: Callable[[ProbeResult], None] | None = None,
) -> ResolvedEndpoint:
    """Resolve an endpoint and only the payload fields that are missing.

    The endpoint and payload branches are independent:

    * an explicit endpoint is never replaced by SBOM, probe, or browser output;
    * an omitted endpoint is resolved from SBOM, then live probing;
    * explicit payload fields constrain probing and are never overwritten;
    * missing payload fields are inferred against the selected endpoint.

    ``UNSET`` is required for omitted fields because ``"message"`` and
    ``False`` are both valid explicit configuration values.
    """
    notes: list[str] = []
    endpoint_is_explicit = endpoint is not UNSET and bool(endpoint)
    resolved_path = str(endpoint) if endpoint_is_explicit else None
    path_source = EndpointSource.CONFIG if endpoint_is_explicit else EndpointSource.UNKNOWN

    key_is_explicit = payload_key is not UNSET
    list_is_explicit = payload_list is not UNSET
    template_is_explicit = value_template is not UNSET
    response_is_explicit = response_key is not UNSET

    resolved_key = str(payload_key) if key_is_explicit else None
    resolved_list = bool(payload_list) if list_is_explicit else None
    resolved_template = value_template if template_is_explicit else None
    resolved_response = (
        str(response_key)
        if response_is_explicit and response_key is not None
        else None
    )
    payload_source = EndpointSource.CONFIG if key_is_explicit or list_is_explicit else EndpointSource.UNKNOWN

    # Static SBOM metadata is the cheapest discovery strategy after config.
    # Tracked separately from resolved_path so that, if this path turns out
    # not to be chat-capable when actually probed below, we know to fall back
    # to full live discovery instead of trusting a possibly-stale SBOM route
    # (the static source scan can lag behind what's actually deployed).
    sbom_path_unvalidated: str | None = None
    if resolved_path is None and sbom is not None:
        try:
            sbom_path, sbom_key, sbom_list, sbom_response = discover_chat_config(
                sbom,
                chat_path=None,
                chat_payload_key=resolved_key or DEFAULT_PAYLOAD_KEY,
                chat_payload_list=resolved_list or DEFAULT_PAYLOAD_LIST,
            )
        except Exception as exc:  # noqa: BLE001 - discovery is best effort
            notes.append(f"SBOM endpoint discovery failed: {exc}")
        else:
            if sbom_path:
                resolved_path = sbom_path
                path_source = EndpointSource.SBOM
                sbom_path_unvalidated = sbom_path
            if not key_is_explicit and sbom_key:
                resolved_key = sbom_key
                payload_source = EndpointSource.SBOM
            if not list_is_explicit:
                resolved_list = bool(sbom_list)
                payload_source = EndpointSource.SBOM
            if not response_is_explicit and sbom_response:
                resolved_response = sbom_response

    # Live probing discovers both path and payload when no path is available.
    probe_result = None
    if resolved_path is None and sbom is not None:
        try:
            probe_result = await probe_endpoint(
                target_url,
                sbom,
                auth_headers=auth_headers,
                timeout=timeout,
                known_payload_key=resolved_key if key_is_explicit else None,
                known_payload_list=resolved_list or DEFAULT_PAYLOAD_LIST,
                known_response_key=resolved_response,
                probe_payload_extras=probe_payload_extras,
                llm=llm,
            )
        except Exception as exc:  # noqa: BLE001 - detector must remain best effort
            notes.append(f"Live endpoint discovery failed: {exc}")
        if probe_result is not None:
            if probe_result_callback is not None:
                probe_result_callback(probe_result)
            resolved_path = probe_result.path
            path_source = EndpointSource.PROBE
            probed_payload = payload_shape_from_probe_result(
                probe_result,
                payload_key=payload_key,
                payload_list=payload_list,
                value_template=value_template,
                response_key=response_key,
            )
            if not key_is_explicit:
                resolved_key = probed_payload.key
            if not list_is_explicit:
                resolved_list = probed_payload.is_list
            if not template_is_explicit:
                resolved_template = probed_payload.value_template
            if not response_is_explicit:
                resolved_response = probed_payload.response_key
            payload_source = probed_payload.source

    # A configured or SBOM-selected endpoint may still need payload inference.
    if sbom is not None and resolved_path is not None and (
        not key_is_explicit
        or not list_is_explicit
        or not template_is_explicit
        or not response_is_explicit
    ):
        is_websocket = sbom is not None and indicates_websocket(
            sbom,
            chat_path=resolved_path,
            chat_payload_key=resolved_key or DEFAULT_PAYLOAD_KEY,
        )
        if is_websocket:
            resolved_key = "__websocket__"
            resolved_list = False
            payload_source = EndpointSource.SBOM
        if not is_websocket and resolved_key != "__websocket__":
            inferred = await detect_payload_shape(
                target_url,
                sbom,
                resolved_path,
                payload_key=payload_key,
                payload_list=payload_list,
                value_template=value_template,
                response_key=response_key,
                auth_headers=auth_headers,
                timeout=timeout,
                probe_payload_extras=probe_payload_extras,
                llm=llm,
                probe_result_callback=probe_result_callback,
            )
            if not key_is_explicit:
                resolved_key = inferred.key
            if not list_is_explicit:
                resolved_list = inferred.is_list
            if not template_is_explicit:
                resolved_template = inferred.value_template
            if not response_is_explicit:
                resolved_response = inferred.response_key
            payload_source = inferred.source
            notes.extend(inferred.notes)

            # The SBOM-derived path didn't validate as chat-capable (probe
            # found nothing usable at it) — the static source scan may be
            # stale relative to what's actually deployed (e.g. a route that
            # moved, or a non-chat route like a GraphQL stub that happens to
            # share a name). Retry with unconstrained live probing over the
            # full candidate list instead of silently keeping a route that
            # explicitly failed validation.
            if sbom_path_unvalidated and inferred.source is EndpointSource.FALLBACK:
                fallback_probe = await probe_endpoint(
                    target_url,
                    sbom,
                    auth_headers=auth_headers,
                    timeout=timeout,
                    known_payload_key=resolved_key if key_is_explicit else None,
                    known_payload_list=resolved_list or DEFAULT_PAYLOAD_LIST,
                    known_response_key=resolved_response,
                    probe_payload_extras=probe_payload_extras,
                    llm=llm,
                )
                if fallback_probe is not None:
                    if probe_result_callback is not None:
                        probe_result_callback(fallback_probe)
                    resolved_path = fallback_probe.path
                    path_source = EndpointSource.PROBE
                    probed_payload = payload_shape_from_probe_result(
                        fallback_probe,
                        payload_key=payload_key,
                        payload_list=payload_list,
                        value_template=value_template,
                        response_key=response_key,
                    )
                    if not key_is_explicit:
                        resolved_key = probed_payload.key
                    if not list_is_explicit:
                        resolved_list = probed_payload.is_list
                    if not template_is_explicit:
                        resolved_template = probed_payload.value_template
                    if not response_is_explicit:
                        resolved_response = probed_payload.response_key
                    payload_source = probed_payload.source
                    notes.append(
                        f"SBOM-derived endpoint {sbom_path_unvalidated!r} did not validate as "
                        f"chat-capable — live probing found {resolved_path!r} instead."
                    )
                else:
                    notes.append(
                        f"SBOM-derived endpoint {sbom_path_unvalidated!r} did not validate as "
                        "chat-capable, and full endpoint probing found no alternative; "
                        "keeping it as a last resort."
                    )

    # Browser detection is deliberately opt-in because it starts a headless browser.
    if resolved_path is None and enable_browser_fallback:
        browser_result = await detect_with_browser(target_url)
        if browser_result is not None:
            resolved_path, browser_payload = browser_result
            path_source = EndpointSource.BROWSER
            if not key_is_explicit:
                resolved_key = browser_payload.key
            if not list_is_explicit:
                resolved_list = browser_payload.is_list
            if not template_is_explicit:
                resolved_template = browser_payload.value_template
            payload_source = EndpointSource.BROWSER

    if resolved_path is None:
        notes.append("No endpoint was resolved.")
        path_source = EndpointSource.UNKNOWN
    if resolved_key is None:
        resolved_key = DEFAULT_PAYLOAD_KEY
        resolved_list = DEFAULT_PAYLOAD_LIST if resolved_list is None else resolved_list
        payload_source = EndpointSource.FALLBACK
    if resolved_list is None:
        resolved_list = DEFAULT_PAYLOAD_LIST

    payload = PayloadShape(
        key=resolved_key,
        is_list=bool(resolved_list),
        value_template=cast(dict[str, Any] | None, resolved_template),
        response_key=resolved_response,
        source=payload_source,
        explicit_key=key_is_explicit,
        explicit_list=list_is_explicit,
        explicit_template=template_is_explicit,
        notes=tuple(notes),
    )
    return ResolvedEndpoint(
        path=resolved_path,
        payload=payload,
        path_source=path_source,
        path_explicit=endpoint_is_explicit,
        notes=tuple(notes),
    )
