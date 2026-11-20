"""In-process WorkerRunner doubles for API tests (no subprocess, no clone)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nuguard.service.dispatcher import EventFn, WorkerOutcome
from nuguard.service.pipeline import JobSpec
from nuguard.service.worker import _stub_result


class FakeRunner:
    """Behaviour per job: "ok", "partial", "hang", or "error:<code>".

    Records the token each job received so tests can check the vault handoff.
    """

    def __init__(self, behaviour: str = "ok") -> None:
        self.behaviour = behaviour
        self.tokens: dict[str, str | None] = {}
        self.specs: dict[str, JobSpec] = {}
        self.started = asyncio.Event()

    async def run(
        self,
        spec: JobSpec,
        token: str | None,
        *,
        scratch: Path,
        env: dict[str, str],
        timeout_s: float,
        cancel: asyncio.Event,
        on_event: EventFn,
    ) -> WorkerOutcome:
        self.tokens[spec.job_id] = token
        self.specs[spec.job_id] = spec
        self.started.set()
        behaviour = self.behaviour
        if behaviour == "hang":
            try:
                await asyncio.wait_for(cancel.wait(), timeout_s)
                return WorkerOutcome(kind="cancelled")
            except asyncio.TimeoutError:
                return WorkerOutcome(kind="timeout")
        if behaviour.startswith("error:"):
            return WorkerOutcome(kind="error", code=behaviour.split(":", 1)[1], detail="boom")
        events: list[dict[str, Any]] = []
        _stub_result(spec, scratch, events.append, partial=behaviour == "partial")
        for event in events:
            await on_event(event)
        last = events[-1]
        return WorkerOutcome(kind="result", result_path=last["path"], sbom_path=last["sbom_path"])
