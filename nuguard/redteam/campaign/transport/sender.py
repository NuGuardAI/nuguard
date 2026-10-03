"""BranchSender: send turns for one branch over the shared pooled client."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nuguard.redteam.target.client import TargetAppClient
    from nuguard.redteam.target.session import AttackSession

    from .context import BranchTransport


class TargetLimiter:
    """Bounds in-flight requests to the target for the whole campaign run.

    Replaces the legacy client-side semaphore (``redteam.max_concurrent_requests``)
    for campaign mode. The slot is held only while a request is in flight: a
    deferred retry raises out of the ``async with`` and frees the slot at once,
    so cooldowns never occupy capacity.
    """

    def __init__(self, max_concurrent_requests: int = 0) -> None:
        self._sem = (
            asyncio.Semaphore(max_concurrent_requests) if max_concurrent_requests > 0 else None
        )

    async def __aenter__(self) -> "TargetLimiter":
        if self._sem is not None:
            await self._sem.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._sem is not None:
            self._sem.release()


class BranchSender:
    """Sends turns/direct requests for one branch; state lives on its transport."""

    def __init__(
        self,
        client: "TargetAppClient",
        transport: "BranchTransport",
        limiter: TargetLimiter | None = None,
    ) -> None:
        self._client = client
        self.transport = transport
        self._limiter = limiter or TargetLimiter()

    async def send(
        self,
        payload: str,
        session: "AttackSession",
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[str, list[dict]]:
        """Send one chat turn. May raise ``RetryDeferred`` (slot already released)."""
        async with self._limiter:
            result = await self._client.send(
                payload, session, extra_headers=extra_headers, transport=self.transport
            )
        self.transport.record_turn(estimated_tokens=(len(payload) + len(result[0])) // 4)
        return result

    async def invoke(
        self,
        path: str,
        method: str = "POST",
        body: dict | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        strip_auth: bool = False,
    ) -> tuple[int, str, dict]:
        """Direct HTTP request under the branch's identity (or without it)."""
        async with self._limiter:
            return await self._client.invoke_endpoint(
                path,
                method=method,
                body=body,
                params=params,
                extra_headers=extra_headers,
                strip_auth=strip_auth,
                transport=self.transport,
            )
