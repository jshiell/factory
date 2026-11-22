from dataclasses import replace

from factory.sqlite_store import SqliteTaskStore
from factory.task import Task, TaskState


def test_update_applies_the_change_persists_it_and_returns_the_new_task(tmp_path):
    store = SqliteTaskStore(tmp_path / "factory.db")
    store.save(Task(id="t1", prompt="", state=TaskState.QUEUED, goal="g"))

    updated = store.update("t1", lambda task: replace(task, goal="changed"))

    assert updated.goal == "changed"
    assert store.load("t1").goal == "changed"
