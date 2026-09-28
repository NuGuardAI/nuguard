"""Shared pre-probe context: resolve the real API origin before any live probing.

Live endpoint probing is only meaningful against the origin that actually
serves the API, with the credentials the eventual client will use. Two things
routinely break that assumption:

* The configured ``target.url`` serves a frontend (static hosting, or an SPA
  whose bundle calls a separate-origin backend directly). Every probe then
  hits the frontend's catch-all route.
* The target authenticates via a login step (JWT/session). Probing with the
  raw configured credentials (e.g. an HTTP Basic header) gets 401s, or 2xx
  HTML from a login redirect, which is useless for discovery.

:func:`resolve_api_origin` runs the static-hosting fallback plus the
frontend-bundle scan once per URL (memoised, since behavior's analyzer and
runner and redteam's orchestrator all need it). :func:`auth_requires_login`
lets callers defer live probing until after auth bootstrap instead of probing
unauthenticated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)

# target_url -> (resolved_origin, notes). Process-lifetime memo: the frontend
# bundle scan is network I/O and several stages resolve the same URL.
_ORIGIN_CACHE: dict[str, tuple[str, list[str]]] = {}


def clear_api_origin_cache() -> None:
    """Drop memoised origin resolutions (used by tests)."""
    _ORIGIN_CACHE.clear()


async def resolve_api_origin(
    target_url: str,
    sbom: "AiSbomDocument | None",
) -> tuple[str, list[str]]:
    """Return ``(api_origin, notes)`` for *target_url*.

    Applies :func:`~nuguard.common.target_client_builder.resolve_target_url`
    (static-hosting → SBOM deployment URL fallback) and then
    :func:`~nuguard.common.endpoint_detection.frontend_origin.discover_api_origin_from_frontend_bundle`.
    When neither changes anything the returned origin equals *target_url*
    (without a trailing slash) and *notes* is empty. Never raises.

    Example:
        >>> origin, notes = await resolve_api_origin("http://app.example", sbom)
        >>> origin
        'http://app.example:3010'
    """
    if not target_url:
        return target_url, []
    cached = _ORIGIN_CACHE.get(target_url)
    if cached is not None:
        return cached[0], list(cached[1])

    from nuguard.common.endpoint_detection.frontend_origin import (  # noqa: PLC0415
        discover_api_origin_from_frontend_bundle,
    )
    from nuguard.common.target_client_builder import resolve_target_url  # noqa: PLC0415

    notes: list[str] = []
    url = target_url
    try:
        resolved, url_notes = resolve_target_url(target_url, sbom)
        if resolved:
            url = resolved
        notes.extend(url_notes)
        bundle_origin, bundle_notes = await discover_api_origin_from_frontend_bundle(url)
        if bundle_origin:
            url = bundle_origin
            notes.extend(bundle_notes)
    except Exception as exc:  # noqa: BLE001 - origin resolution is best effort
        _log.debug("resolve_api_origin: failed for %s: %s", target_url, exc)

    _ORIGIN_CACHE[target_url] = (url, list(notes))
    return url, notes


def auth_requires_login(auth_config: Any, sbom: "AiSbomDocument | None") -> bool:
    """True when authenticating against the target needs a live login step.

    That is the case for an explicit ``login_flow`` config, and for ``basic``
    credentials that :func:`~nuguard.common.target_client_builder.resolve_auth_config_with_sbom_fallback`
    would upgrade to a login flow from an SBOM-discovered login endpoint.
    Callers use this to defer live endpoint probing until after auth
    bootstrap, rather than probing with credentials the target won't accept.
    """
    if auth_config is None:
        return False
    auth_type = getattr(auth_config, "type", "none") or "none"
    if auth_type == "login_flow":
        return True
    if auth_type != "basic" or getattr(auth_config, "login_flow", None) is not None:
        return False
    if sbom is None:
        return False
    try:
        from nuguard.common.target_client_builder import (  # noqa: PLC0415
            resolve_auth_config_with_sbom_fallback,
        )

        upgraded, _note = resolve_auth_config_with_sbom_fallback(auth_config, sbom)
    except Exception as exc:  # noqa: BLE001 - detection is best effort
        _log.debug("auth_requires_login: SBOM upgrade check failed: %s", exc)
        return False
    return getattr(upgraded, "type", None) == "login_flow"
