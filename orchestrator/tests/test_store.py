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
