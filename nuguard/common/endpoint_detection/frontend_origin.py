"""Best-effort discovery of a separate-origin API base URL baked into a served SPA bundle.

Some SPAs call a separate-origin backend directly from client-side JS instead
of proxying through their own server, so SBOM/live-probe discovery can't find
that origin because every path under the served target is handled by the
frontend's catch-all route. This scans the served bundle for a baked-in API
base URL before endpoint discovery/auth bootstrap runs.
"""

from __future__ import annotations

import re

import httpx

from nuguard.common.logging import get_logger

_log = get_logger(__name__)

# Detects a served page that's a bundled SPA (Vite/CRA/webpack), not a JSON API.
_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src=["\']([^"\']+\.js[^"\']*)["\']', re.IGNORECASE)

# Matches the common "baseURL: '<absolute-url>'" shape client bundles bake in.
_API_BASE_URL_RE = re.compile(
    r'(?:baseURL|baseUrl|apiBaseUrl|apiBase|apiUrl|API_URL|API_BASE_URL)\s*[:=]\s*'
    r'[`"\'](https?://[^`"\'\s,)]+)',
)

_MAX_SCRIPTS_TO_SCAN = 6
_SCRIPT_FETCH_TIMEOUT = 15.0

_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


async def discover_api_origin_from_frontend_bundle(
    target_url: str,
) -> "tuple[str | None, list[str]]":
    """Best-effort: find a separate-origin API base URL baked into a served SPA bundle.

    Returns ``(origin, notes)``. *origin* is ``scheme://host[:port]`` when a different
    origin than *target_url* was found baked into a script; otherwise ``None``.
    Never raises — network/parse failures return ``(None, [])``.
    """
    from urllib.parse import urljoin, urlparse  # noqa: PLC0415

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as http_client:
            resp = await http_client.get(target_url)
    except Exception as exc:
        _log.debug("discover_api_origin_from_frontend_bundle: GET %s failed: %s", target_url, exc)
        return None, []

    html = resp.text or ""
    script_srcs = _SCRIPT_SRC_RE.findall(html)[:_MAX_SCRIPTS_TO_SCAN]
    if not script_srcs:
        return None, []

    target_parsed = urlparse(target_url)
    target_host_port = (target_parsed.hostname, target_parsed.port)

    for src in script_srcs:
        script_url = urljoin(target_url.rstrip("/") + "/", src)
        try:
            async with httpx.AsyncClient(
                timeout=_SCRIPT_FETCH_TIMEOUT, follow_redirects=True
            ) as http_client:
                script_resp = await http_client.get(script_url)
        except Exception as exc:
            _log.debug("discover_api_origin_from_frontend_bundle: GET script %s failed: %s", script_url, exc)
            continue

        match = _API_BASE_URL_RE.search(script_resp.text or "")
        if not match:
            continue

        parsed = urlparse(match.group(1))
        if (parsed.hostname, parsed.port) == target_host_port:
            continue

        if (parsed.hostname or "").lower() in _LOOPBACK_HOSTNAMES:
            target_hostname = target_parsed.hostname
            if not target_hostname or parsed.port is None or parsed.port == target_host_port[1]:
                _log.warning(
                    "discover_api_origin_from_frontend_bundle: ignoring loopback origin "
                    "%r baked into %r — not reachable from this process",
                    match.group(1), script_url,
                )
                continue
            scheme = target_parsed.scheme or parsed.scheme
            origin = f"{scheme}://{target_hostname}:{parsed.port}"
            note = (
                f"Target URL {target_url!r} serves a frontend bundle whose baked-in API "
                f"origin {match.group(1)!r} uses a loopback host — reusing target hostname "
                f"with discovered port {parsed.port} instead ({origin!r})."
            )
            _log.warning("discover_api_origin_from_frontend_bundle: %s", note)
            return origin, [note]

        origin = f"{parsed.scheme}://{parsed.netloc}"
        note = (
            f"Target URL {target_url!r} serves a frontend bundle with no proxied API "
            f"surface; discovered API origin {origin!r} baked into {script_url!r}. "
            f"Set target.url in nuguard.yaml to this origin directly to skip this scan."
        )
        _log.warning("discover_api_origin_from_frontend_bundle: %s", note)
        return origin, [note]

    return None, []
