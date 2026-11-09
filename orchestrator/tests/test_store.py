import pytest

from factory.store import InvalidTaskId, TaskNotFound, TaskStore
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


@pytest.mark.parametrize("task_id", ["a/b", "..", "../evil", ""])
def test_unsafe_task_ids_are_rejected(tmp_path, task_id):
    store = TaskStore(tmp_path)

    with pytest.raises(InvalidTaskId):
        store.save(Task(id=task_id, prompt="x"))
    with pytest.raises(InvalidTaskId):
        store.load(task_id)


def test_worktree_and_base_sha_round_trip(tmp_path):
    store = TaskStore(tmp_path)
    task = Task(id="t1", prompt="x", worktree="/w/t1", base_sha="abc123")

    store.save(task)

    assert store.load("t1") == task


def test_json_saved_without_worktree_fields_still_loads(tmp_path):
    (tmp_path / "t1.json").write_text(
        '{"id": "t1", "prompt": "x", "state": "queued", "attempts": 0, "failure_reason": null, "denials": []}'
    )

    task = TaskStore(tmp_path).load("t1")

    assert task.worktree is None
    assert task.base_sha is None


def test_audit_fields_round_trip(tmp_path):
    store = TaskStore(tmp_path)
    task = Task(id="t1", prompt="p", state=TaskState.AUDITED, audit_findings=("a", "b"), audited_sha="abc")
    store.save(task)

    assert store.load("t1") == task


def test_json_saved_without_audit_fields_still_loads(tmp_path):
    (tmp_path / "t1.json").write_text(
        '{"id": "t1", "prompt": "p", "state": "verified", "attempts": 1, "failure_reason": null, "denials": []}'
    )

    task = TaskStore(tmp_path).load("t1")

    assert task.audit_findings == ()
    assert task.audited_sha is None


def test_front_half_fields_round_trip_with_tuples_restored(tmp_path):
    store = TaskStore(tmp_path)
    task = Task(
        id="t1", prompt="", state=TaskState.REVIEWED, goal="add x", plan="do it", plan_risks=("r1",),
        plan_digest="d", plan_error="timeout", review_findings=("f1", "f2"), review_error="timeout",
    )
    store.save(task)

    assert store.load("t1") == task


def test_json_saved_without_front_half_fields_still_loads(tmp_path):
    (tmp_path / "t1.json").write_text(
        '{"id": "t1", "prompt": "p", "state": "queued", "attempts": 0, "failure_reason": null, "denials": []}'
    )

    task = TaskStore(tmp_path).load("t1")

    assert (task.goal, task.plan, task.plan_risks, task.review_findings) == (None, None, (), ())


from datetime import datetime

from factory.store import TaskExists, new_task_id


def test_list_returns_tasks_sorted_by_id(tmp_path):
    store = TaskStore(tmp_path)
    for task_id in ("b", "a", "c"):
        store.save(Task(id=task_id, prompt="p"))

    listing = store.list()

    assert [t.id for t in listing.tasks] == ["a", "b", "c"] and listing.skipped == []


def test_list_of_an_empty_or_missing_directory_is_empty(tmp_path):
    assert TaskStore(tmp_path).list().tasks == []
    assert TaskStore(tmp_path / "missing").list().tasks == []


def test_list_skips_temp_files_and_reports_unreadable_or_unsafe_files(tmp_path):
    store = TaskStore(tmp_path)
    store.save(Task(id="ok", prompt="p"))
    (tmp_path / "x.tmp").write_text("{}")
    (tmp_path / "corrupt.json").write_text("{not json")
    (tmp_path / "bad id.json").write_text("{}")

    listing = store.list()

    assert [t.id for t in listing.tasks] == ["ok"]
    assert listing.skipped == ["bad id.json", "corrupt.json"]


def test_create_refuses_to_overwrite_an_existing_task(tmp_path):
    store = TaskStore(tmp_path)
    store.create(Task(id="t1", prompt="first"))

    with pytest.raises(TaskExists):
        store.create(Task(id="t1", prompt="second"))

    assert store.load("t1").prompt == "first"
    assert [p.name for p in tmp_path.iterdir()] == ["t1.json"]


def test_new_task_id_is_a_safe_timestamp_with_an_optional_suffix():
    now = datetime(2026, 11, 7, 9, 5, 3)

    assert new_task_id(now) == "20261107-090503"
    assert new_task_id(now, "2") == "20261107-090503-2"


def test_waived_findings_round_trip_as_a_tuple(tmp_path):
    store = TaskStore(tmp_path)
    task = Task(id="t1", prompt="p", waived_findings=("Recommend (required): use fsum",))

    store.save(task)

    assert store.load("t1") == task


def test_a_task_file_without_waived_findings_loads_with_none_waived(tmp_path):
    # guard (green on arrival): files saved before plan 29 have no such key
    (tmp_path / "t1.json").write_text(
        '{"id": "t1", "prompt": "p", "state": "queued", "attempts": 0, "failure_reason": null, "denials": []}'
    )

    assert TaskStore(tmp_path).load("t1").waived_findings == ()


def test_prior_audit_findings_round_trip_as_a_tuple(tmp_path):
    store = TaskStore(tmp_path)
    task = Task(id="t1", prompt="p", prior_audit_findings=("the empty list was not handled",))

    store.save(task)

    assert store.load("t1") == task


def test_a_task_file_without_prior_audit_findings_loads_with_none(tmp_path):
    # guard (green on arrival): files saved before plan 30 have no such key
    (tmp_path / "t1.json").write_text(
        '{"id": "t1", "prompt": "p", "state": "queued", "attempts": 0, "failure_reason": null, "denials": []}'
    )

    assert TaskStore(tmp_path).load("t1").prior_audit_findings == ()
