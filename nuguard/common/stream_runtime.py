"""Shared runtime primitives for public streaming APIs."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Generic, TypeVar

from nuguard.common.streaming_models import StreamEvent

T = TypeVar("T")


@dataclass
class _StreamController:
    run_id: str
    queue_maxsize: int = 256
    _queue: asyncio.Queue[StreamEvent] = field(init=False)
    _sequence: int = field(default=0, init=False)
    _terminal_emitted: bool = field(default=False, init=False)
    _closed: bool = field(default=False, init=False)
    _final_result: asyncio.Future[Any] = field(init=False)

    def __post_init__(self) -> None:
        self._queue = asyncio.Queue(maxsize=self.queue_maxsize)
        self._final_result = asyncio.get_running_loop().create_future()

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def publish(self, *, event_type: str, phase: str, payload: dict[str, Any] | None = None) -> None:
        if self._closed:
            return
        if self._terminal_emitted and event_type not in ("completed", "failed"):
            return
        event = StreamEvent(
            event_type=event_type,  # type: ignore[arg-type]
            run_id=self.run_id,
            sequence=self._next_sequence(),
            phase=phase,
            payload=payload or {},
        )
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Drop only low-priority updates under pressure.
            if event_type in ("heartbeat", "scenario_progress"):
                return
            # Best-effort: make room by dropping one old low-priority event.
            drained: list[StreamEvent] = []
            dropped = False
            while not self._queue.empty():
                existing = self._queue.get_nowait()
                if (not dropped) and existing.event_type in ("heartbeat", "scenario_progress"):
                    dropped = True
                    continue
                drained.append(existing)
            for item in drained:
                self._queue.put_nowait(item)
            self._queue.put_nowait(event)

    def publish_terminal(self, *, event_type: str, phase: str, payload: dict[str, Any] | None = None) -> None:
        if self._terminal_emitted:
            return
        self._terminal_emitted = True
        self.publish(event_type=event_type, phase=phase, payload=payload)

    async def events(self) -> AsyncIterator[StreamEvent]:
        while True:
            event = await self._queue.get()
            yield event
            if event.event_type in ("completed", "failed"):
                self._closed = True
                break

    def set_final_result(self, value: Any) -> None:
        if not self._final_result.done():
            self._final_result.set_result(value)

    def set_final_exception(self, exc: BaseException) -> None:
        if not self._final_result.done():
            self._final_result.set_exception(exc)

    async def final_result(self) -> Any:
        return await self._final_result


class StreamRunHandle(Generic[T]):
    """Public stream handle returned by streaming entrypoints."""

    def __init__(self, controller: _StreamController, task: asyncio.Task[None]) -> None:
        self._controller = controller
        self._task = task

    @property
    def events(self) -> AsyncIterator[StreamEvent]:
        return self._controller.events()

    async def final_result(self) -> T:
        return await self._controller.final_result()

    def cancel(self) -> None:
        self._task.cancel()


def create_stream_handle(
    run_id: str,
    task_coro: Callable[[_StreamController], Coroutine[Any, Any, None]],
) -> StreamRunHandle[Any]:
    """Create a stream controller and schedule the worker coroutine."""
    controller = _StreamController(run_id=run_id)
    task = asyncio.create_task(task_coro(controller))
    return StreamRunHandle(controller, task)
