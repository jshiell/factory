import pytest

from factory.store import TaskNotFound, TaskStore
from factory.task import Task, TaskState


def test_saved_task_loads_back_equal(tmp_path):
    store = TaskStore(tmp_path)
    task = Task(id="t1", prompt="do it", state=TaskState.FAILED, attempts=2, failure_reason="x")

    store.save(task)

    assert store.load("t1") == task


def test_loading_a_missing_task_raises_task_not_found(tmp_path):
    with pytest.raises(TaskNotFound):
        TaskStore(tmp_path).load("nope")
