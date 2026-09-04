"""Shared runtime primitives for public streaming APIs."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Generic, TypeVar

from nuguard.common.streaming_models import StreamEvent

T = TypeVar("T")


class StreamExecutionError(RuntimeError):
    """Sanitized public error raised when a stream worker fails."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class StreamCancelledError(asyncio.CancelledError):
    """Sanitized cancellation error preserving CancelledError compatibility."""


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
            # Make room for lifecycle and terminal events. Prefer a low-priority
            # event, but discard the oldest event when the queue has none.
            drained: list[StreamEvent] = []
            dropped = False
            while not self._queue.empty():
                existing = self._queue.get_nowait()
                if (not dropped) and existing.event_type in ("heartbeat", "scenario_progress"):
                    dropped = True
                    continue
                drained.append(existing)
            if not dropped and drained:
                drained.pop(0)
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

    @property
    def final_result_settled(self) -> bool:
        return self._final_result.done()

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

    async def wait_closed(self, timeout: float | None = None) -> None:
        """Wait for worker shutdown without cancelling it when the wait times out."""
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)
        except asyncio.CancelledError:
            if self._task.cancelled():
                return
            raise


def create_stream_handle(
    run_id: str,
    task_coro: Callable[[_StreamController], Coroutine[Any, Any, None]],
    *,
    heartbeat_interval: float | None = None,
) -> StreamRunHandle[Any]:
    """Create a stream controller and schedule the worker coroutine."""
    controller = _StreamController(run_id=run_id)

    async def _heartbeat() -> None:
        assert heartbeat_interval is not None
        while True:
            await asyncio.sleep(heartbeat_interval)
            controller.publish(event_type="heartbeat", phase="runtime", payload={})

    async def _run() -> None:
        heartbeat_task = (
            asyncio.create_task(_heartbeat())
            if heartbeat_interval is not None and heartbeat_interval > 0
            else None
        )
        try:
            await task_coro(controller)
        except asyncio.CancelledError as exc:
            if not controller.final_result_settled:
                controller.publish_terminal(
                    event_type="failed",
                    phase="finalize",
                    payload={
                        "status": "failed",
                        "failure_stage": "cancelled",
                        "error_type": "stream_cancelled",
                        "error_message": "Stream execution cancelled",
                    },
                )
                controller.set_final_exception(StreamCancelledError("Stream execution cancelled"))
            raise exc
        except Exception:
            if not controller.final_result_settled:
                controller.publish_terminal(
                    event_type="failed",
                    phase="finalize",
                    payload={
                        "status": "failed",
                        "failure_stage": "runtime",
                        "error_type": "stream_execution_failed",
                        "error_message": "Stream execution failed",
                    },
                )
                controller.set_final_exception(
                    StreamExecutionError("stream_execution_failed", "Stream execution failed")
                )
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

    task = asyncio.create_task(_run())

    def _settle_unstarted_task(completed_task: asyncio.Task[None]) -> None:
        if completed_task.cancelled() and not controller.final_result_settled:
            controller.publish_terminal(
                event_type="failed",
                phase="finalize",
                payload={
                    "status": "failed",
                    "failure_stage": "cancelled",
                    "error_type": "stream_cancelled",
                    "error_message": "Stream execution cancelled",
                },
            )
            controller.set_final_exception(StreamCancelledError("Stream execution cancelled"))

    task.add_done_callback(_settle_unstarted_task)
    return StreamRunHandle(controller, task)
