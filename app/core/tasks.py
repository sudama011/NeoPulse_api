"""Supervised background tasks: a failing iteration is logged and retried, never silently fatal."""

import asyncio
import contextlib
import logging
from typing import Awaitable, Callable, Coroutine

logger = logging.getLogger("Tasks")


class TaskSupervisor:
    def __init__(self):
        self._tasks: list[asyncio.Task] = []

    def periodic(self, name: str, interval: float | Callable[[], float], fn: Callable[[], Awaitable[None]]) -> None:
        """Runs `fn` every `interval` seconds (interval may be a callable for adaptive pacing)."""

        async def loop():
            while True:
                try:
                    await fn()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Background job '%s' failed; will retry", name)
                await asyncio.sleep(interval() if callable(interval) else interval)

        self._tasks.append(asyncio.create_task(loop(), name=name))

    def spawn(self, name: str, coro: Coroutine[None, None, None]) -> None:
        self._tasks.append(asyncio.create_task(coro, name=name))

    async def shutdown(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks.clear()


def fire_and_forget(coro: Awaitable[None], _pending: set = set()) -> None:
    """Schedules a coroutine and keeps a strong reference until it finishes."""
    task = asyncio.ensure_future(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)
