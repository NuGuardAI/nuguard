"""Shared 429 rate-limit detection and scenario-level backoff helpers.

``TargetAppClient`` handles 429 at the HTTP-request level (up to 2 retries with
exponential backoff + ``Retry-After`` header support).  These helpers add a
*second tier*: scenario/step-level backoff that fires when all request-level
retries are exhausted and the target is still returning 429.

Usage pattern
-------------
Both ``BehaviorRunner`` and ``AttackExecutor`` import from this module so the
retry logic and constants are defined in exactly one place.

    from nuguard.common.rate_limit import (
        SCENARIO_MAX_RATE_LIMIT_RETRIES,
        is_rate_limited,
        scenario_rate_limit_backoff,
    )
"""
from __future__ import annotations

import asyncio
import logging
import random

_log = logging.getLogger(__name__)

# Sentinel prefix emitted by TargetAppClient when a 429 exhausts all
# per-request retries.
_RATE_LIMIT_PREFIX = "[HTTP 429]"

# Scenario-level 429 defaults (layered *on top of* TargetAppClient's own
# per-request retry loop).
SCENARIO_MAX_RATE_LIMIT_RETRIES: int = 3
SCENARIO_RATE_LIMIT_BACKOFF_BASE: float = 2.0   # seconds
SCENARIO_RATE_LIMIT_BACKOFF_CAP: float = 30.0   # seconds


def is_rate_limited(response: str) -> bool:
    """Return ``True`` when TargetAppClient returned a 429 sentinel.

    This happens after all per-request retries inside TargetAppClient are
    exhausted and the target is *still* responding with HTTP 429.
    """
    return response.startswith(_RATE_LIMIT_PREFIX)


async def scenario_rate_limit_backoff(
    attempt: int,
    *,
    base_seconds: float = SCENARIO_RATE_LIMIT_BACKOFF_BASE,
    cap_seconds: float = SCENARIO_RATE_LIMIT_BACKOFF_CAP,
    context: str = "",
) -> float:
    """Sleep with capped exponential backoff + jitter for scenario-level 429.

    Args:
        attempt: Zero-based retry attempt number (0 = first retry).
        base_seconds: Base delay before exponential growth.
        cap_seconds: Maximum sleep duration.
        context: Optional context string for the warning log message.

    Returns:
        Actual sleep duration in seconds (useful for tests).
    """
    delay = min(base_seconds * (2 ** attempt), cap_seconds)
    jitter = random.uniform(0.0, delay * 0.25)
    total = round(delay + jitter, 2)
    _log.warning(
        "Rate limited (429) at scenario level%s — backing off %.1fs "
        "(attempt %d/%d)",
        f" [{context}]" if context else "",
        total,
        attempt + 1,
        SCENARIO_MAX_RATE_LIMIT_RETRIES,
    )
    await asyncio.sleep(total)
    return total
