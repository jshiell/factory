"""A stop request that ends open event streams, settable from any thread or a signal handler."""

import asyncio
import contextlib


class StopRequest:
    """Once requested, stays requested: every current and later `wait()` returns.

    uvicorn waits for open connections before it runs the lifespan shutdown, so
    the request comes from its signal hooks, not from the lifespan. The event is
    only set on the attached loop's thread (through `call_soon_threadsafe`).
    """

    def __init__(self) -> None:
        self._requested = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._event = asyncio.Event()

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind to the running loop; call from that loop, before any waiter."""
        self._event = asyncio.Event()
        # Publish the loop before reading the flag, the reverse of `request`, so a concurrent request is never lost.
        self._loop = loop
        if self._requested:
            self._event.set()

    def request(self) -> None:
        """Ask streams to end; safe from any thread, before attach and after the loop closed, and never raises."""
        self._requested = True
        loop = self._loop
        if loop is None:
            return
        with contextlib.suppress(RuntimeError):  # a closed loop; the flag still covers the next attach
            loop.call_soon_threadsafe(self._set)

    def is_requested(self) -> bool:
        return self._requested

    def _set(self) -> None:
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()
