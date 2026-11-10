import pytest

from factory.sqlite_store import SqliteTaskStore
from factory.ids import InvalidTaskId
from factory.store import TaskExists, TaskNotFound
from factory.task import Task, TaskState


def test_saved_task_with_every_field_set_loads_back_equal(tmp_path):
    store = SqliteTaskStore(tmp_path / "factory.db")
    task = Task(
        id="t1", prompt="do it", state=TaskState.AUDITED, attempts=2, failure_reason="x",
        denials=({"path": "/etc", "op": "read"},), worktree="/w", base_sha="abc",
        audit_findings=("a", "b"), audited_sha="def", audit_error="e", goal="g", plan="p",
        plan_risks=("r",), plan_digest="d", plan_error="pe", review_findings=("rf",),
        review_error="re", retry_of="t0", fix_rounds_used=1, fix_in_flight=True,
        fix_frozen_sha="fff", waived_findings=("w",), prior_audit_findings=("pa",),
    )

    store.save(task)

    assert store.load("t1") == task


def test_create_never_overwrites_an_existing_task(tmp_path):
    store = SqliteTaskStore(tmp_path / "factory.db")
    store.create(Task(id="t1", prompt="first"))

    with pytest.raises(TaskExists):
        store.create(Task(id="t1", prompt="second"))

    assert store.load("t1").prompt == "first"


def test_loading_a_missing_task_raises_task_not_found(tmp_path):
    with pytest.raises(TaskNotFound):
        SqliteTaskStore(tmp_path / "factory.db").load("nope")


@pytest.mark.parametrize("task_id", ["a/b", "..", "../evil", ""])
def test_unsafe_task_ids_are_rejected(tmp_path, task_id):
    store = SqliteTaskStore(tmp_path / "factory.db")

    with pytest.raises(InvalidTaskId):
        store.load(task_id)
    with pytest.raises(InvalidTaskId):
        store.save(Task(id=task_id, prompt="p"))
    with pytest.raises(InvalidTaskId):
        store.create(Task(id=task_id, prompt="p"))
