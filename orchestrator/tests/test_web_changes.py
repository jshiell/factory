import asyncio
import threading

from factory.web.changes import TaskChanges

TIMEOUT = 5


def _notify_from_thread(changes: TaskChanges, times: int = 1) -> None:
    def run():
        for _ in range(times):
            changes.notify()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()


def test_wait_past_returns_after_notify_from_another_thread():
    async def scenario():
        changes = TaskChanges()
        changes.attach(asyncio.get_running_loop())
        start = changes.version
        waiter = asyncio.create_task(changes.wait_past(start))
        await asyncio.sleep(0)  # let the waiter block before the write lands
        _notify_from_thread(changes)
        return start, await asyncio.wait_for(waiter, TIMEOUT)

    start, seen = asyncio.run(scenario())

    assert seen > start


def test_wait_past_an_old_version_returns_at_once():
    async def scenario():
        changes = TaskChanges()
        changes.attach(asyncio.get_running_loop())
        start = changes.version
        _notify_from_thread(changes)
        # the bump is queued with call_soon_threadsafe; one loop turn runs it
        await asyncio.sleep(0)
        return start, await asyncio.wait_for(changes.wait_past(start), TIMEOUT)

    start, seen = asyncio.run(scenario())

    assert seen > start


def test_notify_before_attach_does_not_raise():
    TaskChanges().notify()


def test_notify_after_the_loop_is_closed_does_not_raise():
    changes = TaskChanges()
    loop = asyncio.new_event_loop()
    changes.attach(loop)
    loop.close()

    _notify_from_thread(changes)
    changes.notify()


def test_many_notifies_each_bump_the_version_once():
    async def scenario():
        changes = TaskChanges()
        changes.attach(asyncio.get_running_loop())
        start = changes.version
        threads = [
            threading.Thread(target=_notify_from_thread, args=(changes, 50))
            for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return start, await asyncio.wait_for(changes.wait_past(start + 199), TIMEOUT)

    start, seen = asyncio.run(scenario())

    assert seen == start + 200


def test_a_cancelled_waiter_does_not_stop_other_waiters_waking():
    async def scenario():
        changes = TaskChanges()
        changes.attach(asyncio.get_running_loop())
        start = changes.version
        leaver = asyncio.create_task(changes.wait_past(start))
        stayer = asyncio.create_task(changes.wait_past(start))
        await asyncio.sleep(0)
        leaver.cancel()
        await asyncio.gather(leaver, return_exceptions=True)
        _notify_from_thread(changes)
        return start, await asyncio.wait_for(stayer, TIMEOUT), leaver.cancelled()

    start, seen, leaver_cancelled = asyncio.run(scenario())

    assert seen > start
    assert leaver_cancelled


def test_waiters_work_after_attaching_to_a_new_loop():
    changes = TaskChanges()

    async def abandoned_wait():
        changes.attach(asyncio.get_running_loop())
        waiter = asyncio.create_task(changes.wait_past(changes.version))
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)

    async def wait_on_new_loop():
        changes.attach(asyncio.get_running_loop())
        start = changes.version
        waiter = asyncio.create_task(changes.wait_past(start))
        await asyncio.sleep(0)
        _notify_from_thread(changes)
        return start, await asyncio.wait_for(waiter, TIMEOUT)

    asyncio.run(abandoned_wait())
    start, seen = asyncio.run(wait_on_new_loop())

    assert seen > start


def test_notify_from_the_loop_thread_wakes_a_waiter():
    async def scenario():
        changes = TaskChanges()
        changes.attach(asyncio.get_running_loop())
        start = changes.version
        waiter = asyncio.create_task(changes.wait_past(start))
        await asyncio.sleep(0)
        changes.notify()
        return start, await asyncio.wait_for(waiter, TIMEOUT)

    start, seen = asyncio.run(scenario())

    assert seen > start
