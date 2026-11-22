import pytest

from factory.abandon_task import AbandonRefused, abandon_task
from factory.store import TaskStore
from factory.task import Task, TaskState
from updateonly import UpdateOnly


@pytest.fixture
def inner(tmp_path):
    (tmp_path / "tasks").mkdir()
    return TaskStore(tmp_path / "tasks")


def test_abandoning_a_task_writes_through_update_not_save(inner):
    inner.save(Task(id="t1", prompt="", state=TaskState.REVIEWED, goal="g"))
    store = UpdateOnly(inner)

    abandoned = abandon_task("t1", store)

    assert abandoned.state is TaskState.FAILED
    assert store.updates == ["t1"]
    assert inner.load("t1") == abandoned


def test_a_refused_abandon_goes_through_update_and_leaves_the_task_unchanged(inner):
    inner.save(Task(id="t1", prompt="", state=TaskState.MERGED, goal="g"))
    store = UpdateOnly(inner)

    with pytest.raises(AbandonRefused):
        abandon_task("t1", store)

    assert inner.load("t1").state is TaskState.MERGED
