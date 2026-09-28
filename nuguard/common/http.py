"""Shared httpx client factory with timeout and retry configuration."""

from __future__ import annotations

import re

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


# Response-body wording apps use when a usage quota / plan limit / credit
# balance is exhausted. Such responses often reuse 403 (or 402/429), but the
# credentials are fine — re-authenticating cannot help.
QUOTA_EXHAUSTED_RE = re.compile(
    r"quota|plan limit|usage limit|credit limit|request limit"
    r"|limit (?:reached|exceeded)|reached (?:your|the) [\w\s-]{0,40}limit"
    r"|exceeded (?:your|the) [\w\s-]{0,40}(?:limit|quota)"
    r"|insufficient (?:credits?|balance|funds)|out of credits|upgrade your (?:plan|subscription)",
    re.IGNORECASE,
)

QUOTA_STATUS_CODES: frozenset[int] = frozenset({402, 403, 429})


# Every quota_exhausted_detail() note starts with this, so callers can
# recognise the classification from an error_detail string.
QUOTA_EXHAUSTED_PREFIX = "Target usage quota exhausted"


def quota_exhausted_detail(status_code: int, body: str) -> str:
    """Return a human-readable note when *body* says a usage quota is exhausted.

    Only 402/403/429 responses qualify. Returns ``""`` otherwise, so callers
    can keep their normal auth/rate-limit handling.

    Example:
        >>> quota_exhausted_detail(403, '{"message": "You\'ve reached your free plan limit"}')
        'Target usage quota exhausted (HTTP 403) ...'
    """
    if status_code not in QUOTA_STATUS_CODES or not body:
        return ""
    match = QUOTA_EXHAUSTED_RE.search(body[:2000])
    if match is None:
        return ""
    return (
        f"{QUOTA_EXHAUSTED_PREFIX} (HTTP {status_code}): {body[:200]} — the "
        "credentials are valid, but the test account has hit the app's plan/usage "
        "limit. Raise or reset the account's quota (e.g. upgrade its plan) and re-run."
    )
