"""Server-Sent Events (SSE) parser utilities.

Shared between the production TargetAppClient and test helpers so both use
the same parsing logic for ADK ``/run_sse`` responses.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import httpx

_log = logging.getLogger(__name__)


def parse_sse_events(raw: str) -> list[dict[str, Any]]:
    """Parse a Server-Sent Events (SSE) body into a list of JSON event objects.

    ADK's ``/run_sse`` endpoint emits lines like::

        data: {"author":"MarketResearcher","content":{...}}\\n\\n

    Each non-empty ``data:`` line is parsed as a JSON object and collected.
    Lines that cannot be decoded as JSON (e.g. ``event: ping``) are silently
    skipped.

    Args:
        raw: The raw SSE text body.

    Returns:
        A list of parsed JSON objects (dicts). Empty list when *raw* is empty
        or contains no valid data lines.
    """
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload:
            continue
        try:
            obj = json.loads(payload)
            if isinstance(obj, dict):
                events.append(obj)
        except json.JSONDecodeError:
            _log.debug("SSE: skipping non-JSON data line: %.80r", payload)
    return events


async def iter_sse_events(response: httpx.Response) -> AsyncGenerator[dict[str, Any], None]:
    """Iterate over SSE events from a streaming *response*.

    Designed for use with ``httpx.AsyncClient.stream()``.  Yields each parsed
    JSON event dict as it arrives.  Empty or non-data lines are skipped.

    Args:
        response: An active ``httpx.Response`` in streaming mode.

    Yields:
        Parsed JSON event dicts.
    """
    async for line in response.aiter_lines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload:
            continue
        try:
            obj = json.loads(payload)
            if isinstance(obj, dict):
                yield obj
        except json.JSONDecodeError:
            _log.debug("iter_sse_events: skipping non-JSON data line: %.80r", payload)
