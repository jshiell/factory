from factory.approve_plan import approve_plan
from factory.digest import plan_digest
from factory.store import TaskStore
from factory.task import Task, TaskState
from updateonly import UpdateOnly

PLAN = "1. add the thing"


def test_approving_a_plan_writes_through_update_not_save(tmp_path):
    (tmp_path / "tasks").mkdir()
    inner = TaskStore(tmp_path / "tasks")
    inner.save(Task(id="t1", prompt="", state=TaskState.REVIEWED, goal="g", plan=PLAN, plan_digest=plan_digest(PLAN)))
    store = UpdateOnly(inner)

    queued = approve_plan("t1", store, plan_digest(PLAN), ())

    assert queued.state is TaskState.QUEUED
    assert store.updates == ["t1"]
    assert inner.load("t1") == queued
