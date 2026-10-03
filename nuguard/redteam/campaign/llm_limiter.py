"""One shared attacker-provider limiter with per-purpose accounting.

Planning, enrichment, mutation, guided generation and evaluation all go through
the same concurrency limit so a campaign cannot exceed the provider budget by
fanning out across purposes. Target rate/concurrency limits are separate
(:class:`~nuguard.redteam.campaign.transport.TargetLimiter`).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class PurposeUsage:
    calls: int = 0
    prompt_chars: int = 0
    completion_chars: int = 0

    @property
    def est_tokens(self) -> int:
        return (self.prompt_chars + self.completion_chars) // 4


class LLMLimiter:
    """Wraps an ``LLMClient``-like object: ``complete`` is bounded and accounted per purpose."""

    def __init__(self, llm: Any, max_concurrent: int = 4) -> None:
        self._llm = llm
        self._sem = asyncio.Semaphore(max(1, max_concurrent))
        self.usage: dict[str, PurposeUsage] = {}

    async def complete(
        self, prompt: str, system: str = "", label: str = "", *, purpose: str = "", **kwargs: Any
    ) -> str:
        key = purpose or (label.split("|")[0].strip() if label else "other")
        async with self._sem:
            reply: str = await self._llm.complete(prompt, system=system, label=label, **kwargs)
        u = self.usage.setdefault(key, PurposeUsage())
        u.calls += 1
        u.prompt_chars += len(prompt) + len(system)
        u.completion_chars += len(reply or "")
        return reply

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)

    def totals(self) -> PurposeUsage:
        t = PurposeUsage()
        for u in self.usage.values():
            t.calls += u.calls
            t.prompt_chars += u.prompt_chars
            t.completion_chars += u.completion_chars
        return t
