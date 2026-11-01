"""Egress-callback canary server (redteam-proposal.md W8).

Generalizes :class:`~nuguard.redteam.executor.poison_server.PoisonPayloadServer`'s
existing ``/trap`` + ``trap_hits()`` mechanism — already a working prototype
of an egress-callback trap — into three typed roles so SSRF and covert-exfil
scenarios can get hard, externally-verifiable proof of a primitive instead of
judging success from the chat answer alone:

1. **SSRF proof** (:meth:`ssrf_proof_url`) — substitute as the fetch target in
   a tool-abuse SSRF scenario; a received request confirms the agent's
   backend actually made the egress call, and the request headers/source IP
   reveal what it leaks (the proposal's Pinnacle example: a ``CipherBank-
   Agent/1.0`` UA + an internal egress IP).
2. **Covert-exfil proof** (:meth:`exfil_proof_url`) — substitute for the
   ``example.com`` placeholder in C-series URL-exfil scenarios; a hit
   confirms the exact encoded payload the agent chose to leak.
3. **Beacon / metadata** (:meth:`beacon_url`) — a generic callback role for
   scenarios that just need "did anything call back", without an SSRF/exfil
   framing.

This is a thin composition wrapper, not a rewrite: ``PoisonPayloadServer``
keeps serving injection payloads to indirect-injection/MCP-toxic-flow/RAG
scenarios exactly as before; this class adds the three typed role URLs plus
:meth:`hit_records_since` for the executor's success-detection poll.
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from nuguard.redteam.executor.poison_server import PoisonPayloadServer, TrapHit

CanaryRole = Literal["ssrf", "exfil", "beacon", "generic"]

# How long the executor should poll for a callback after sending a payload —
# the target's backend makes the egress call asynchronously, so the hit may
# not have landed yet when our chat response returns. Centralized here
# (rather than duplicated in executor.py) since it's a property of how this
# server is used, not of the step-execution loop itself.
POLL_INTERVAL_SECONDS = 0.3
POLL_MAX_ATTEMPTS = 5  # ~1.5s total — bounded so a miss doesn't stall the chain


class CallbackCanaryServer:
    """Typed-role facade over :class:`PoisonPayloadServer` for W8 evidence."""

    def __init__(self, app_name: str = "application") -> None:
        self._server = PoisonPayloadServer(app_name=app_name)

    @property
    def host(self) -> str:
        return self._server.host

    @property
    def port(self) -> int:
        return self._server.port

    @property
    def netloc(self) -> str:
        return self._server.netloc

    async def __aenter__(self) -> "CallbackCanaryServer":
        await self._server.start()
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self._server.stop()

    def ssrf_proof_url(self) -> str:
        """URL to substitute as an SSRF scenario's fetch target."""
        return self._server.url("/trap/ssrf")

    def exfil_proof_url(self, encoding: str = "raw") -> str:
        """URL to substitute for a covert-exfil scenario's placeholder host.

        ``encoding`` is informational only (recorded in the step's resolved
        payload, not parsed server-side) — callers already know which
        encoding they applied to the data before building the URL.
        """
        return self._server.url(f"/trap/exfil/{encoding}")

    def beacon_url(self) -> str:
        """Generic callback URL — "did anything call back at all"."""
        return self._server.url("/trap/beacon")

    def hit_records(self) -> list[TrapHit]:
        return self._server.trap_hit_records()

    def hit_records_since(self, t0: float, role: CanaryRole | None = None) -> list[TrapHit]:
        return self._server.trap_hit_records_since(t0, role=role)

    async def poll_for_hit(
        self, t0: float, role: CanaryRole,
        interval: float = POLL_INTERVAL_SECONDS,
        max_attempts: int = POLL_MAX_ATTEMPTS,
    ) -> TrapHit | None:
        """Poll for a hit of *role* received at/after *t0*, bounded by *max_attempts*.

        Returns the first matching hit, or ``None`` if none arrived within
        the poll window — the step then falls back to whatever other
        success detection it has configured (keyword/LLM eval).
        """
        for attempt in range(max_attempts):
            hits = self.hit_records_since(t0, role=role)
            if hits:
                return hits[0]
            if attempt < max_attempts - 1:
                await asyncio.sleep(interval)
        return None
