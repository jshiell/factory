import asyncio
import threading

from factory.web.stopping import StopRequest

TIMEOUT = 5


def _request_from_thread(stopping: StopRequest) -> None:
    thread = threading.Thread(target=stopping.request)
    thread.start()
    thread.join()


def test_wait_returns_after_a_request_from_another_thread():
    async def scenario():
        stopping = StopRequest()
        stopping.attach(asyncio.get_running_loop())
        waiter = asyncio.create_task(stopping.wait())
        await asyncio.sleep(0)  # let the waiter block before the request lands
        _request_from_thread(stopping)
        await asyncio.wait_for(waiter, TIMEOUT)

    asyncio.run(scenario())


def test_a_request_before_attach_is_kept_for_the_attached_loop():
    stopping = StopRequest()
    stopping.request()

    async def scenario():
        stopping.attach(asyncio.get_running_loop())
        await asyncio.wait_for(stopping.wait(), TIMEOUT)

    asyncio.run(scenario())
    assert stopping.is_requested()


def test_a_request_after_the_loop_closed_does_not_raise_and_reaches_the_next_loop():
    stopping = StopRequest()

    async def attach():
        stopping.attach(asyncio.get_running_loop())

    asyncio.run(attach())
    stopping.request()

    async def scenario():
        stopping.attach(asyncio.get_running_loop())
        await asyncio.wait_for(stopping.wait(), TIMEOUT)

    asyncio.run(scenario())
