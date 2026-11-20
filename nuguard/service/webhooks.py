"""Optional completion callbacks (off unless ``service.webhooks.allowed_hosts`` is set).

On a terminal status the service POSTs the ``ScanJob`` (never the full result)
to the job's ``callback_url`` with ``X-NuGuard-Signature: sha256=<HMAC-SHA256(secret, body)>``
and ``X-NuGuard-Timestamp``. Delivery never changes the job status.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import socket
import time
from urllib.parse import urlsplit

import httpx

from nuguard.common.logging import get_logger
from nuguard.service.settings import WebhookSettings

_log = get_logger("nuguard.service.webhooks")

SIGNATURE_HEADER = "X-NuGuard-Signature"
TIMESTAMP_HEADER = "X-NuGuard-Timestamp"


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def host_allowed(url: str, settings: WebhookSettings) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return bool(host) and host in {h.lower().rstrip(".") for h in settings.allowed_hosts}


def _resolves_public(host: str, port: int) -> bool:
    """Every address *host* resolves to must be public (blocks DNS rebinding to internals)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_multicast or address.is_reserved or address.is_unspecified):
            return False
    return True


async def deliver(
    url: str,
    body: bytes,
    settings: WebhookSettings,
    *,
    attempts: int = 3,
    base_delay_s: float = 1.0,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """POST *body* to *url*; True on a 2xx. Never raises."""
    if settings.secret is None or not host_allowed(url, settings):
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        return False
    if host not in {h.lower() for h in settings.allow_private_hosts}:
        if not await asyncio.to_thread(_resolves_public, host, parts.port or 443):
            _log.warning("webhook host resolves to a non-public address; not delivering")
            return False
    secret = settings.secret.get_secret_value()
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=10.0, follow_redirects=False)
    try:
        for attempt in range(attempts):
            timestamp = str(int(time.time()))
            headers = {
                "Content-Type": "application/json",
                SIGNATURE_HEADER: sign(secret, body),
                TIMESTAMP_HEADER: timestamp,
            }
            try:
                response = await http.post(url, content=body, headers=headers)
                if 200 <= response.status_code < 300:
                    return True
            except httpx.HTTPError:
                pass
            if attempt + 1 < attempts:
                await asyncio.sleep(base_delay_s * (2 ** attempt))
        _log.warning("webhook delivery failed after %d attempts", attempts)
        return False
    finally:
        if own_client:
            await http.aclose()
