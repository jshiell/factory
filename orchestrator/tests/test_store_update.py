from dataclasses import replace

from factory.store import TaskStore
from factory.task import Task, TaskState


def test_file_store_update_applies_the_change_persists_it_and_returns_the_new_task(tmp_path):
    (tmp_path / "tasks").mkdir()
    store = TaskStore(tmp_path / "tasks")
    store.save(Task(id="t1", prompt="", state=TaskState.QUEUED, goal="g"))

    updated = store.update("t1", lambda task: replace(task, goal="changed"))

    assert updated.goal == "changed"
    assert store.load("t1").goal == "changed"
