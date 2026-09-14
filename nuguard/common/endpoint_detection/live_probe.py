"""Live endpoint and payload probing adapters.

The implementation remains in ``common.endpoint_probe`` during migration. This
module gives the new package a stable, documented boundary for callers.
"""

from __future__ import annotations

from typing import Any

from nuguard.common.endpoint_probe import ProbeResult


def normalize_probe_result(result: ProbeResult | None) -> ProbeResult | None:
    """Return a probe result unchanged while documenting the package boundary."""
    return result


async def probe_endpoint(
    target_url: str,
    sbom: Any,
    *,
    auth_headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    known_payload_key: str | None = None,
    known_payload_list: bool = False,
    known_response_key: str | None = None,
    probe_payload_extras: dict[str, object] | None = None,
    hint_path: str | None = None,
    llm: Any = None,
) -> ProbeResult | None:
    """Probe candidate routes and return endpoint plus payload metadata.

    This is the package entry point for full endpoint discovery and for targeted
    payload discovery when ``hint_path`` is supplied.
    """
    from nuguard.common.endpoint_probe import probe_chat_endpoints

    return await probe_chat_endpoints(
        target_url=target_url,
        sbom=sbom,
        auth_headers=auth_headers,
        timeout=timeout,
        known_payload_key=known_payload_key,
        known_payload_list=known_payload_list,
        known_response_key=known_response_key,
        probe_payload_extras=probe_payload_extras,
        hint_path=hint_path,
        llm=llm,
    )


async def probe_payload_shape(
    target_url: str,
    sbom: Any,
    endpoint: str,
    *,
    auth_headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    known_payload_list: bool = False,
    known_response_key: str | None = None,
    probe_payload_extras: dict[str, object] | None = None,
    llm: Any = None,
) -> ProbeResult | None:
    """Infer payload details for one known endpoint without changing its path."""
    return await probe_endpoint(
        target_url,
        sbom,
        auth_headers=auth_headers,
        timeout=timeout,
        known_payload_key=None,
        known_payload_list=known_payload_list,
        known_response_key=known_response_key,
        probe_payload_extras=probe_payload_extras,
        hint_path=endpoint,
        llm=llm,
    )
