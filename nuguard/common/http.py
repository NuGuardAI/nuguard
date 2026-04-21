"""Shared httpx client factory with timeout and retry configuration."""

from __future__ import annotations

import httpx

from nuguard import __version__


def make_http_client(timeout: float = 30.0, retries: int = 3) -> httpx.AsyncClient:
    """Create a configured async httpx client.

    Args:
        timeout: Request timeout in seconds.
        retries: Number of times to retry on transient failure (5xx / network
                 errors).  Implemented via a custom transport.

    Returns:
        An ``httpx.AsyncClient`` ready for use as an async context manager.
    """
    transport = httpx.AsyncHTTPTransport(retries=retries)
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        transport=transport,
        headers={
            "User-Agent": f"nuguard/{__version__} (https://github.com/NuGuardAI/nuguard-oss)",
        },
        follow_redirects=True,
    )
