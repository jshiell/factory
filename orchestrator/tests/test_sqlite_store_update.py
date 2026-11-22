from dataclasses import replace

import pytest

from factory.ids import InvalidTaskId
from factory.sqlite_store import SqliteTaskStore
from factory.store import TaskNotFound
from factory.task import Task, TaskState


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


def test_update_of_an_unsafe_id_raises_invalid_task_id(tmp_path):
    # guard (green on arrival)
    store = SqliteTaskStore(tmp_path / "factory.db")

    with pytest.raises(InvalidTaskId):
        store.update("../x", lambda task: task)
