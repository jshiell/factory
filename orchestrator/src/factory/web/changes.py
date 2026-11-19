"""A change counter that wakes asyncio waiters when the task store writes."""

import asyncio
import contextlib


class TaskChanges:
    """Counts task store writes and wakes coroutines waiting for the next one.

    `version` and the current event are touched only on the attached loop's
    thread (`_bump` runs there through `call_soon_threadsafe`), so they need no
    lock. Each bump sets the current event and replaces it: a waiter re-checks
    `version` after waking, and a cancelled waiter only drops its own future.
    """

    def __init__(self) -> None:
        self.version = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._changed = asyncio.Event()

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind to the running loop; call from that loop, before any waiter."""
        # A fresh event: one left unset by an abandoned waiter stays bound to the old loop.
        self._changed = asyncio.Event()
        self._loop = loop

    def notify(self) -> None:
        """Record a write; safe from any thread, and never raises."""
        loop = self._loop
        if loop is None:
            return
        # A closed loop raises RuntimeError; a stage may still write after shutdown.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._bump)

    def _bump(self) -> None:
        self.version += 1
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    async def wait_past(self, version: int) -> int:
        """Return the current version once it exceeds `version`; at once if it already does."""
        while self.version <= version:
            await self._changed.wait()
        return self.version
