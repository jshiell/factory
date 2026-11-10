import sqlite3

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


def test_list_returns_tasks_sorted_by_id_and_an_empty_store_lists_nothing(tmp_path):
    store = SqliteTaskStore(tmp_path / "factory.db")
    assert store.list().tasks == []
    for task_id in ("b", "a", "c"):
        store.save(Task(id=task_id, prompt="p"))

    listing = store.list()

    assert [t.id for t in listing.tasks] == ["a", "b", "c"] and listing.skipped == []


def test_list_skips_and_reports_a_row_that_cannot_be_read(tmp_path):
    db = tmp_path / "factory.db"
    store = SqliteTaskStore(db)
    store.save(Task(id="ok", prompt="p"))
    with sqlite3.connect(db) as conn:  # simulates a damaged row; no public way to write one
        conn.execute("INSERT INTO tasks (id, state, data) VALUES ('corrupt', 'queued', '{not json')")
        conn.execute("INSERT INTO tasks (id, state, data) VALUES ('partial', 'queued', '{}')")

    listing = store.list()

    assert [t.id for t in listing.tasks] == ["ok"]
    assert listing.skipped == ["corrupt", "partial"]


def test_save_replaces_an_existing_task(tmp_path):
    # guard (green on arrival): save already is INSERT OR REPLACE
    store = SqliteTaskStore(tmp_path / "factory.db")
    store.save(Task(id="t1", prompt="old"))

    store.save(Task(id="t1", prompt="new", state=TaskState.FAILED))

    assert store.load("t1").prompt == "new" and store.load("t1").state is TaskState.FAILED
    assert len(store.list().tasks) == 1
