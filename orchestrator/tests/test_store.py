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


def test_failed_replace_leaves_previous_content_and_no_temp_files(tmp_path, monkeypatch):
    store = TaskStore(tmp_path)
    original = Task(id="t1", prompt="old")
    store.save(original)

    def boom(src, dst):
        raise OSError("disk gone")

    monkeypatch.setattr("factory.store.os.replace", boom)
    with pytest.raises(OSError):
        store.save(Task(id="t1", prompt="new"))
    monkeypatch.undo()

    assert store.load("t1") == original
    assert [p.name for p in tmp_path.iterdir()] == ["t1.json"]
