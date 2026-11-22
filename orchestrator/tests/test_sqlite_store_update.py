import threading
import time
from dataclasses import replace

import pytest

from factory.ids import InvalidTaskId
from factory.sqlite_store import SqliteTaskStore
from factory.store import TaskNotFound
from factory.task import Event, InvalidTransition, Task, TaskState, apply


def test_update_applies_the_change_persists_it_and_returns_the_new_task(tmp_path):
    store = SqliteTaskStore(tmp_path / "factory.db")
    store.save(Task(id="t1", prompt="", state=TaskState.QUEUED, goal="g"))

    updated = store.update("t1", lambda task: replace(task, goal="changed"))

    assert updated.goal == "changed"
    assert store.load("t1").goal == "changed"


def test_update_of_a_missing_task_raises_task_not_found_without_calling_change(tmp_path):
    # guard (green on arrival): the shared read already raises it
    store = SqliteTaskStore(tmp_path / "factory.db")
    calls = []

    with pytest.raises(TaskNotFound):
        store.update("nope", lambda task: calls.append(task) or task)

    assert calls == []


def test_an_exception_in_change_leaves_the_stored_task_unchanged(tmp_path):
    # guard (green on arrival): the transaction rolls back
    store = SqliteTaskStore(tmp_path / "factory.db")
    store.save(Task(id="t1", prompt="", state=TaskState.QUEUED, goal="g"))

    def failing(task):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        store.update("t1", failing)

    assert store.load("t1").goal == "g"


def test_two_updates_on_one_task_from_separate_stores_leave_exactly_one_winner(tmp_path):
    # the first change holds the task after reading it until the second update has been started
    path = tmp_path / "factory.db"
    SqliteTaskStore(path).save(Task(id="t1", prompt="", state=TaskState.REVIEWED, goal="g"))
    first_has_read, release_first = threading.Event(), threading.Event()
    outcomes = {}

    def approve_holding(task):
        first_has_read.set()
        release_first.wait(timeout=5)
        return apply(task, Event.APPROVED)

    def run(name, change):
        try:
            SqliteTaskStore(path).update("t1", change)
            outcomes[name] = "won"
        except InvalidTransition:
            outcomes[name] = "lost"
        except Exception as error:  # a lock error would land here
            outcomes[name] = repr(error)

    first = threading.Thread(target=run, args=("first", approve_holding))
    second = threading.Thread(target=run, args=("second", lambda task: apply(task, Event.APPROVED)))
    first.start()
    assert first_has_read.wait(timeout=5)
    second.start()
    time.sleep(0.2)  # let the second reach the lock; the outcome is the same if it is slow
    release_first.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert outcomes == {"first": "won", "second": "lost"}


def test_subscribers_are_told_once_per_successful_update_and_not_after_a_failure(tmp_path):
    # guard (green on arrival)
    store = SqliteTaskStore(tmp_path / "factory.db")
    store.save(Task(id="t1", prompt="", state=TaskState.QUEUED, goal="g"))
    calls = []
    store.subscribe(lambda: calls.append(1))

    store.update("t1", lambda task: replace(task, goal="a"))
    with pytest.raises(InvalidTransition):
        store.update("t1", lambda task: apply(task, Event.APPROVED))

    assert calls == [1]


def test_update_of_an_unsafe_id_raises_invalid_task_id(tmp_path):
    # guard (green on arrival)
    store = SqliteTaskStore(tmp_path / "factory.db")

    with pytest.raises(InvalidTaskId):
        store.update("../x", lambda task: task)
