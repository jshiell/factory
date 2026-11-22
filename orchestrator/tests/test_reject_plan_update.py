from factory.approve_plan import reject_plan
from factory.store import TaskStore
from factory.task import Task, TaskState
from updateonly import UpdateOnly


def test_rejecting_a_plan_writes_through_update_not_save(tmp_path):
    (tmp_path / "tasks").mkdir()
    inner = TaskStore(tmp_path / "tasks")
    inner.save(Task(id="t1", prompt="", state=TaskState.REVIEWED, goal="g"))
    store = UpdateOnly(inner)

    rejected = reject_plan("t1", store)

    assert rejected.state is TaskState.FAILED
    assert store.updates == ["t1"]
    assert inner.load("t1") == rejected
